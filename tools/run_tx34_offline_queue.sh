#!/usr/bin/env bash
# Trusted local offline-only queue entrypoint. No radio, SDR or serial device.
set -euo pipefail
cd /storage5/dev/flux-tx34-step-response-20261124
test "${K6_EXPERIMENT_OFFLINE:-1}" = 1 || { echo 'offline-only runner flag missing' >&2; exit 2; }
test -x /home/tyler/.local/share/flux-rf-venv/bin/python
exec /home/tyler/.local/share/flux-rf-venv/bin/python -m pytest -q \
    tests/test_k6_tx34_step_response.py tests/test_k6_rf_backend.py
