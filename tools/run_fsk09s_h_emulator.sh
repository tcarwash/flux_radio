#!/usr/bin/env bash
# Mandatory REAL Cortex-M0 Thumb Unicorn execution for FSK-09S-H offline tests.
# Uses the local uv package cache ONLY. No network, radio/SDR/serial access,
# firmware flashing, or side-effects on the shared K6 hardware.
set -euo pipefail
cd "$(dirname "$0")/.."
uv run --offline --no-project \
    --with 'unicorn==2.1.4' --with 'pytest==9.2.0' --with 'numpy==2.5.5' --with 'scipy' \
    python -c 'import unicorn; from unicorn import arm_const; print("Cortex-M0 emulator:", unicorn.__version__)'
uv run --offline --no-project \
    --with 'unicorn==2.1.4' --with 'pytest==9.2.0' --with 'numpy==2.5.5' --with 'scipy' \
    python -m pytest -q -rs \
      tests/test_k6_fsk_bchip_ae_emulator.py \
      tests/test_k6_fsk_bchip_ae_end_to_end_emulator.py \
      tests/test_k6_fsk_bchip_ae_qual.py \
      tests/test_k6_fsk_refill.py \
      tests/test_k6_fsklab.py \
      tests/test_k6_fsk_boundary_bench.py
