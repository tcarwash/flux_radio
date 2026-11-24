# Flux RF research — shared experiment queue and acknowledgment

**Canonical queue:** [Flux RF shared experiment queue — issue #11](https://github.com/tcarwash/flux_radio/issues/11)  
**Date:** 2026-11-23  
**Coordination status:** *Acknowledgment from active RF experiment agent requested; not yet confirmed.*

This file is a breadcrumb for any research agent working on native-FSK, DCS, tone gain, voice-band AFSK, Sotto DV, or related K6 modulation experiments. The queue lives in GitHub Issues so separate local worktrees and ChatGPT threads can refer to the same durable record.

## What changed

We created one umbrella issue and four individual experiments:

- **[#7 — FD6818B FIFO almost-empty interrupt audit](https://github.com/tcarwash/flux_radio/issues/7)** — priority P0, offline/read-only analysis; confirm whether `REG_02` interrupt event decoding explains missing FSK-09 refill signaling. *No bug confirmed.*
- **[#8 — FIFO refill / idle stuffing simulator](https://github.com/tcarwash/flux_radio/issues/8)** — priority P0, offline. Model preloads, append timing, thresholds, underflow and frame-length termination.
- **[#9 — Physical one-word mid-TX refill](https://github.com/tcarwash/flux_radio/issues/9)** — priority P1, **BLOCKED until explicit radio lease**. Test on dummy load with SDR capture, known data, bounded TX and restoration.
- **[#10 — Packetized Sotto voice over native-FSK bursts](https://github.com/tcarwash/flux_radio/issues/10)** — priority P1, offline model for robust low-latency DV with framing, FEC and jitter buffer.

## Message to the currently active RF test agent

**Please reply on issue #11 with a short acknowledgment and the location of your active research branch/worktree.** Suggested format:

```text
ACK | thread/agent: <identifier> | worktree: <path or branch>
Current activity: <what you are testing>
Radio lease: HELD / FREE / UNKNOWN
Queue agreement: YES / CHANGES REQUESTED
Suggested next experiment(s): #...
```

An ACK means you have **read the queue and agree to consult it**; it does **not** transfer hardware control, assign you experiments, authorize flashing or TX, or require you to interrupt what is running. Feel free to add your current TX-15/TX-16/DCS/tone/other experiments to the queue whenever convenient. Post negative as well as positive findings.

## Safety / coexistence

1. Existing ongoing tests and their uncommitted files have priority. Do not reset, flash, transmit, kill processes, reconfigure serial/SDR, or edit another agent's working files on the basis of this document.
2. Read the queue and recent comments before claiming a new task. Use isolated branches/worktrees for code changes.
3. Offline work can run without a radio reservation. Physical RF work requires an **explicitly agreed, exclusive** lease from whoever currently controls the radio, and documented cleanup.
4. Treat GitHub issues as a **human/agent coordination log, not a lock service**; GitHub does not automatically connect independent chat agents or notify them outside their normal subscription/issue-following workflow.
5. Distinguish bench-demonstrated RF results, emulator-only results, and hypotheses. Link measurements, captured IQ/SDR data, source commits and test procedures.

**Do not claim the other agent has accepted coordination until there is an actual ACK in #11.**
