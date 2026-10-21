#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
venv_path="${EPISCOPE_VENV:-/tmp/episcope_venv}"
if ! "$venv_path/bin/python" -c 'import pyspark, numpy, pandas, pyarrow, scipy, rasterio, requests' >/dev/null 2>&1; then
  bash scripts/setup_wsl.sh
fi
export PYSPARK_PYTHON="$venv_path/bin/python"
export SPARK_LOCAL_IP=127.0.0.1
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1
exec "$venv_path/bin/python" -m orchestration.cli "$@"
