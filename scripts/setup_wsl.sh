#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
venv_path="${EPISCOPE_VENV:-/tmp/episcope_venv}"
command -v java >/dev/null || { echo 'Install OpenJDK 11 or 17, then rerun.' >&2; exit 1; }
mkdir -p work
if ! test -x "$venv_path/bin/python"; then
  python3 -m venv "$venv_path" || true
fi
# Ubuntu installations without python3-venv still create the interpreter.
# Bootstrap pip from its official source; no administrator access is required.
if ! "$venv_path/bin/python" -m pip --version >/dev/null 2>&1; then
  curl --fail --location --retry 3 https://bootstrap.pypa.io/get-pip.py -o work/get-pip.py
  "$venv_path/bin/python" work/get-pip.py pip==25.3
fi
"$venv_path/bin/python" -m pip install -r requirements.txt
echo "Runtime ready: $venv_path"
