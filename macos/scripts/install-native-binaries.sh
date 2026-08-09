#!/bin/zsh

set -euo pipefail

repo_root=${0:A:h:h:h}
runtime_bin=${repo_root}/macos/.runtime/bin
temporary_directory=$(mktemp -d "${TMPDIR:-/private/tmp}/frigate-binaries.XXXXXX")
go2rtc_version=1.9.14
go2rtc_archive=go2rtc_mac_arm64.zip
go2rtc_sha256=919b78adc759d6b3883d1e1b2ac915ac0985bb903ff1897b4d228527bd64690c

function cleanup() {
  if [[ -n ${temporary_directory} && ${temporary_directory} == */frigate-binaries.* ]]; then
    find "${temporary_directory}" -depth -delete
  fi
}
trap cleanup EXIT

mkdir -p -m 700 "${runtime_bin}"

gh release download "v${go2rtc_version}" \
  --repo AlexxIT/go2rtc \
  --pattern "${go2rtc_archive}" \
  --dir "${temporary_directory}"

actual_sha256=$(shasum -a 256 "${temporary_directory}/${go2rtc_archive}" | awk '{print $1}')
if [[ ${actual_sha256} != ${go2rtc_sha256} ]]; then
  print -u2 "go2rtc checksum mismatch"
  exit 1
fi

unzip -jo "${temporary_directory}/${go2rtc_archive}" go2rtc \
  -d "${runtime_bin}"
chmod 755 "${runtime_bin}/go2rtc"

print "Installed go2rtc ${go2rtc_version} at ${runtime_bin}/go2rtc"
