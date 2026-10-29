#!/usr/bin/env bash
# Run inside the locked review venv. Does not connect to Discord or Ollama.
set -euo pipefail
cd -- "$(dirname -- "$0")/.."
python -m pip check
python -m unittest discover -s tests -v
python tests_gifmode.py .
bash -n setup.sh
bash -n deploy/collect-review-evidence.sh
if command -v ruff >/dev/null; then ruff check --select E9,F .; fi
