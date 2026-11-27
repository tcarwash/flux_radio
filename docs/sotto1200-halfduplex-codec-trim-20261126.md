# Sotto 1200-only, half-duplex memory and firmware-size evidence

2026-11-26 PST, research branch `optimize/sotto1200-half-duplex-20261126`. **This branch is not a flash-qualified image.** All changes are opt-in. No radio was modified by this branch.

## Which rates can be removed?

Keep two distinct rates: **Sotto 1200**, the voice codec; **4800-baud RF FSK**, the physical transport for FDV1 packets. Also keep the shared 8kHz PCM I/O and the *v3.7 fixed-point synthesis core*, because the actual `sotto_lr` 1200 decoder calls that core to synthesize its A/B halves. Removing v3.7 packet acceptance is safe for a 1200-only profile, but **removing the v3.7 decoder core itself is not**.

The existing build selector `K6_CODEC=1200` already avoids linking the 600/400 decoder and hiss units. A native ARM `bench.elf` link comparison shows:

| Build | `.text` | `.rodata` | `.data` | `.bss` | Flash `.text+.rodata+.data` |
| --- | ---: | ---: | ---: | ---: | ---: |
| Sotto 1200 + 600/400 (`K6_CODEC=vlr`) | 21,344 | 60,292 | 24 | 300 | 81,660 B |
| Sotto 1200 (`K6_CODEC=1200`) | 18,572 | 37,977 | 20 | 284 | 56,569 B |
| Strict 1200 packet acceptance (new) | 18,548 | 37,977 | 20 | 284 | 56,545 B |

These are separately linked **bench** images, not incremental app flash costs. `K6_CODEC=1200` already saves 25,091B versus the VLR build; rejecting the other codec packet magics in `sp_packet/sp_check` saves **24B** further. The optional `K6_SOTTO1200_ONLY=1` mode rejects v3.7, 600/400, and their parity magics while accepting 1200 `0xA9` and `0xB9` parity. It does **not** change the 4800-baud FSK driver. Raw results: [size table](evidence/sotto1200-multirate-arm-size-20261126.json).

## Encoder/decoder arena reuse

The expanded RAM0 host has `ARENA_BYTES=14,976`: 4,096B receive message store, 1,280B PWM audio ring, 9,600B tail for decoder state. Existing `SP_DEC_POOL` shares the entire arena with runtime modules when no receive playback is running; `sp_init()` clears the store and forgets old decoder state before playback. The 1200 decoder's **allocation budget is 3,232B** (2,912B core with in-state synthesis workspace plus 320B LR state). The C host memory-reuse regression compiles the production pager, calls the real `sp_dec_need`, allocates its entire budget, simulates a module write over the arena, calls `sp_init`, then re-allocates and checks that memory is zeroed and the exact same address was reused. This tests allocator and lifetime assumptions; it does not prove race-free IRQ/DMA behavior. The four-block active GP0 microphone ring must not be overlapped with a running TX encoder.

The FD6818B lean voice service had a bug: it omitted both `ffi::sp_dec_pool`/`ffi::sp_dec_need` FFI declarations *and* the pool initialization even if Sotto voice service was enabled. `apply-sotto1200-exclusive-pool.py` gates those two changes to explicit `K6_SOTTO1200_ONLY=1`, `K6_SOTTO_SERVICE=1`, `K6_RAM0=1`. The build now links, and the 1200-only C regression tests pass.

## Hard blocker: MCU flash

**One unified FD6818B firmware with the full in-MCU Sotto1200 decoder needs 149,540B, exceeding the 122,880B application limit by 26,660B.** This is a complete build after fixing the pool/FFI compiler errors, not a guess. The build intentionally refused to output a flashable image. The generated ELF has `.text=128,352`, `.rodata=20,188`, `.data=796`, `.bss=17,076`, `.ram0=3,768` bytes, but the app flash gate—not simply the ELF section sum—is authoritative. Do not disable the bound check or flash the ELF directly.

Focused regressions: **36 passing** tests spanning the new parser/pool guards and the existing TX module/virtual RF tests. Newly added tests:
`tests/test_k6_sotto1200_exclusive.py`, `tests/test_k6_sotto1200_halfduplex_pool.py`.

**Conclusion:** Half-duplex RAM reuse is already present, and preserving it is essential, but it does not reduce persistent flash. To deploy two-way 1200 voice on the K6, move most of the voice decoder synthesis and/or playback into a module or system SPI code page, and verify that its own code, scratch, PCM ring, state lifetime and live IRQ path fit concurrently. Do not overwrite the smaller physically qualified synthetic-TX host merely to load an oversized RX build. The active installed module slot 28 `SOTTO RX` is a native RF receiver and requires API31, **not a proven speaker decoder**.
