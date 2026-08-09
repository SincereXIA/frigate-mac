#!/bin/zsh

set -euo pipefail

repo_root=${0:A:h:h:h}
runtime_bin=${repo_root}/macos/.runtime/bin
build_root=$(mktemp -d "${TMPDIR:-/private/tmp}/frigate-native-nginx.XXXXXX")

function cleanup() {
  if [[ -n ${build_root} && ${build_root} == */frigate-native-nginx.* ]]; then
    find "${build_root}" -depth -delete
  fi
}
trap cleanup EXIT

typeset -A sources
typeset -A checksums
sources[nginx]='https://nginx.org/download/nginx-1.27.4.tar.gz'
checksums[nginx]='294816f879b300e621fa4edd5353dd1ec00badb056399eceb30de7db64b753b2'
sources[vod]='https://github.com/kaltura/nginx-vod-module/archive/refs/tags/1.31.tar.gz'
checksums[vod]='ace04201cf2d2b1a3e5e732a22b92225b8ce61a494df9cc7f79d97efface8952'
sources[secure_token]='https://github.com/kaltura/nginx-secure-token-module/archive/refs/tags/1.5.tar.gz'
checksums[secure_token]='9809b7797e049429627a2c9e78c78e4b27bf4a2d0c7274a126cb6bb16ddd4215'
sources[ndk]='https://github.com/vision5/ngx_devel_kit/archive/refs/tags/v0.3.3.tar.gz'
checksums[ndk]='faa2fcd5168b10764d35081356511d5f84db5c526a1aa4b6add2db94b6853b2b'
sources[set_misc]='https://github.com/openresty/set-misc-nginx-module/archive/refs/tags/v0.33.tar.gz'
checksums[set_misc]='cd5e2cc834bcfa30149e7511f2b5a2183baf0b70dc091af717a89a64e44a2985'
sources[openssl]='https://github.com/openssl/openssl/releases/download/openssl-3.3.2/openssl-3.3.2.tar.gz'
checksums[openssl]='2e8a40b01979afe8be0bbfb3de5dc1c6709fedb46d6c89c10da114ab5fc3d281'

for name url in ${(kv)sources}; do
  archive=${build_root}/${name}.tar.gz
  curl --fail --location --silent --show-error --retry 3 \
    --connect-timeout 15 --output "${archive}" "${url}"
  actual=$(shasum -a 256 "${archive}" | awk '{print $1}')
  if [[ ${actual} != ${checksums[$name]} ]]; then
    print -u2 "Checksum mismatch for ${name}"
    exit 1
  fi
  mkdir -p "${build_root}/${name}"
  tar -xzf "${archive}" -C "${build_root}/${name}" --strip-components=1
done

perl -0pi -e 's/MAX_CLIPS \(128\)/MAX_CLIPS (1080)/' \
  "${build_root}/vod/vod/media_set.h"
perl -0pi -e \
  's/(avc_hevc_parser_rbsp_trailing_bits\(bit_reader_state_t\* reader\)\n\{)/$1\n\treturn TRUE;/' \
  "${build_root}/vod/vod/avc_hevc_parser.c"

pcre2_prefix=$(brew --prefix pcre2)
install_prefix=${build_root}/install
build_log=${build_root}/build.log
cd "${build_root}/nginx"
if ! ./configure \
  --prefix="${install_prefix}" \
  --with-http_sub_module \
  --with-http_ssl_module \
  --with-http_auth_request_module \
  --with-http_realip_module \
  --with-threads \
  --add-module="${build_root}/ndk" \
  --add-module="${build_root}/set_misc" \
  --add-module="${build_root}/vod" \
  --add-module="${build_root}/secure_token" \
  --with-openssl="${build_root}/openssl" \
  --with-openssl-opt='no-shared no-tests' \
  --with-cc=clang \
  --with-cc-opt="-arch arm64 -O3 -mcpu=apple-m1 -Wno-error=implicit-fallthrough -Wno-error=unused-but-set-variable -Wno-error=unused-variable -Wno-error=deprecated-declarations -I${pcre2_prefix}/include" \
  --with-ld-opt="-arch arm64 -L${pcre2_prefix}/lib" \
  >"${build_log}" 2>&1; then
  tail -n 80 "${build_log}"
  exit 1
fi

if ! make -j "$(sysctl -n hw.logicalcpu)" >>"${build_log}" 2>&1; then
  tail -n 80 "${build_log}"
  exit 1
fi
if ! make install >>"${build_log}" 2>&1; then
  tail -n 80 "${build_log}"
  exit 1
fi
mkdir -p "${runtime_bin}"
install -m 755 "${install_prefix}/sbin/nginx" "${runtime_bin}/nginx"
codesign --force --sign - "${runtime_bin}/nginx"

"${runtime_bin}/nginx" -V 2>&1 | grep -Eq -- '--add-module=[^ ]*/vod( |$)'
file "${runtime_bin}/nginx" | grep -q 'Mach-O 64-bit executable arm64'
print "Native nginx with nginx-vod-module installed at ${runtime_bin}/nginx"
