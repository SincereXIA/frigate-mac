#!/bin/zsh

set -euo pipefail

repo_root=${0:A:h:h:h}
python=${repo_root}/.venv-macos/bin/python

if [[ ! -x ${python} ]]; then
  print -u2 "Missing .venv-macos. Create it with python3.13 -m venv .venv-macos"
  exit 1
fi

python_version=$(${python} -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
if [[ ${python_version} != "3.13" ]]; then
  print -u2 "Expected Python 3.13, found ${python_version}"
  exit 1
fi

${python} -m pip install --timeout 60 --retries 8 \
  -r ${repo_root}/macos/requirements-runtime.txt

# Norfair 2.3 works with the APIs Frigate uses, but its metadata declares
# NumPy <2. Python 3.13 has no NumPy 1.26 wheel, so install the pure-Python
# package without asking pip to downgrade NumPy.
${python} -m pip install --timeout 60 --retries 8 --no-deps 'norfair==2.3.*'
