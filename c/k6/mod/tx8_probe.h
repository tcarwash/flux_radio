/* Test-vector-only 600 baud Gray 8-FSK reference. SPDX-License-Identifier: MIT */
#ifndef FLUX_TX8_PROBE_H
#define FLUX_TX8_PROBE_H
#define TX8_BAUD 600u
#define TX8_NS 64u
#define TX8_WORD_COUNT 8u
/* 450 + 300 * tone Hz, converted to REG_71 words at 10.32444 words/Hz. */
static const uint16_t tx8_words[TX8_WORD_COUNT] = {
  4646u, 7743u, 10841u, 13938u, 17035u, 20133u, 23230u, 26327u
};
/* Fixed Gray 8-FSK symbol-vector: first eight map 0..7, followed by a
   deterministic transition stress sequence. This is NOT encoded payload. */
static const uint8_t tx8_symbols[TX8_NS] = {
  0,1,2,3,4,5,6,7, 7,6,5,4,3,2,1,0,
  0,7,1,6,2,5,3,4, 4,3,5,2,6,1,7,0,
  0,2,4,6,1,3,5,7, 7,5,3,1,6,4,2,0,
  1,4,7,2,5,0,3,6, 6,3,0,5,2,7,4,1
};
#endif
