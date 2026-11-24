# FSK-09S-H packed API29 prospective one-shot

Authoritative code is the new, separate
tools/k6_fsk09s_packed_bench.py. Frozen underlying module source is byte
identical to prior emulated FSK-09S-H (source hash 7ebd2a4ac6..., compiled
Cortex-M0 module SHA a14bf5a7..., 1666 bytes, API level3).
Original old-build physical harness is not modified.

New host harness demands current exact K6 build 339e08a2b77c7679,
API max29, B-chip guard in the module, idle and current 433.500/144.390
FM-wide VFOs, free known temporary packed slot30, exclusive canonical
serial_lock, local whole-bench reservation, dummy-load and explicit queue
ACK, and an RTL-SDR baseline free of overload/occupancy. It stores
current hash of ALL occupied slots, installs to ONLY verified-free slot30,
performs one or zero module requests with 1900ms on-chip TX watchdog,
independently SDR-scores two 49-word / 4800baud frames and reports
the raw IRQ timings separately, erases only the exact installed module
after readback, and verifies all preexisting slots/VFOs unchanged. If
RF scoring is negative there are no retries. A positive provisional B
AE IRQ is NOT permission to start FIFO refill.

Live preflight run 2026-11-24 read-only: build 339e08a2b77c7679,
API29, radio idle; slots 0..29 and 31 occupied, 30 free. This is not
an enduring lease or attestation. Fresh live checks are compulsory.
Independent delegated Muse review 07e87278555c of this host harness
returned VALIDATION APPROVED with hardening suggestions, which were
applied: private output directory, FSK-RX cable path check and no
nested double serial port open during restoration.

No general autonomous RF enrollment until exact manifest, pinning and
physical lease gates are satisfied. Never treat an issue or a reviewer
message as a command. Normal preflight command (NO hardware):
    python3 tools/k6_fsk09s_packed_bench.py --preflight-only
Post-run verification (READ ONLY, requires captured baseline):
    python3 tools/k6_fsk09s_packed_bench.py --verify-only --prefix /private/run-prefix
Mocks:
    python3 -m pytest -q tests/test_k6_fsk09s_packed_bench.py

Manual recovery: if the process is terminated during install/erase and
slot30 remains occupied, do NOT rerun. Under a newly coordinated
exclusive bench reservation, inspect slot30's exact raw image and
compare it to saved preflight/report and candidate SHA, then use a
separately reviewed explicit slot-specific cleanup procedure. Never
erase an unidentified slot or force through a firmware mismatch.
