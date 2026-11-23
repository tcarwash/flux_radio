/* TX-15R: 192-word, 150/s NONCOMMENSURATE audio-tone RFIC vector.
 * Dedicated 48-symbol pilot contains >=12 widely-spaced strong tone changes.
 * Heldout: 144 symbols, repeated known tone-pair transitions, 3-word
 * dwell. Observe tone phase and analog frequency-step transfer.
 * Header data only; use the guarded opt-in TX8 module.
 * SPDX-License-Identifier: MIT */
#ifndef FLUX_TX8_LONG_PHASE_PROBE_H
#define FLUX_TX8_LONG_PHASE_PROBE_H
#define TX8_NS 192u
#define TX8_PILOT 48u
#define TX8_WORD_COUNT 8u
static const uint16_t tx8_words[TX8_WORD_COUNT] = {
  5183u,8074u,11264u,14702u,17479u,20515u,23467u,26007u
};
static const uint8_t tx8_symbols[TX8_NS] = {
  5,5,5,7,7,7,0,0,0,6,6,6,
  0,0,0,7,7,7,1,1,1,6,6,6,
  2,2,2,7,7,7,3,3,3,6,6,6,
  4,4,4,7,7,7,0,0,0,6,6,6,
  0,0,0,7,7,7,0,0,0,6,6,6,
  2,2,2,5,5,5,1,1,1,6,6,6,
  1,1,1,7,7,7,3,3,3,4,4,4,
  0,0,0,7,7,7,0,0,0,6,6,6,
  2,2,2,5,5,5,1,1,1,6,6,6,
  1,1,1,7,7,7,3,3,3,4,4,4,
  0,0,0,7,7,7,0,0,0,6,6,6,
  2,2,2,5,5,5,1,1,1,6,6,6,
  1,1,1,7,7,7,3,3,3,4,4,4,
  0,0,0,7,7,7,0,0,0,6,6,6,
  2,2,2,5,5,5,1,1,1,6,6,6,
  1,1,1,7,7,7,3,3,3,4,4,4,
};
#endif
