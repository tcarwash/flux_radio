/* TX-15P: non-commensurate phase-discriminator vector, 150 updates/s.
 * Off by default in tx8probe.c; this header is data, not a TX module.
 * Unlike TX8 classic, f*T is non-integer for every tone. Same-word
 * rewrites and frequency changes can distinguish phase reset policies.
 * A separate, attended, bounded conducted RF bench is still required
 * to obtain physical evidence. SPDX-License-Identifier: MIT */
#ifndef FLUX_TX8_PHASE_PROBE_H
#define FLUX_TX8_PHASE_PROBE_H
#define TX8_BAUD 150u
#define TX8_NS 64u
#define TX8_WORD_COUNT 8u
/* f (Hz): 502, 782, 1091, 1424, 1693, 1987, 2273, 2519.
 * REG_71 encodes f x 10.32444, rounded to nearest integer. */
static const uint16_t tx8_words[TX8_WORD_COUNT] = {
  5183u, 8074u, 11264u, 14702u, 17479u, 20515u, 23467u, 26007u
};
/* Pilot includes repeated writes (0/2/4/6/7) and changed-word
 * transitions; final 48 symbol IDs are held out from phase/timing
 * calibration. No claim of RF phase continuity from this reference. */
static const uint8_t tx8_symbols[TX8_NS] = {
  0,0,1,2,2,3,4,4, 5,6,6,7,0,7,7,1,
  0,0,0,7,7,7,1,1, 1,6,6,6,2,2,2,5,
  5,5,3,3,3,4,4,4, 7,7,0,0,6,6,1,1,
  5,5,2,2,4,4,3,3, 7,7,7,0,0,6,6,1
};
#endif
