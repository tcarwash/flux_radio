/* TX-15T STEER B: same 72 distributed pilot + 6 tail guard as TX-15S.
 * 192 words at 150/s: four 18-symbol pilots spread through the burst,
 * two changed three-write blocks at 177-182; deliberately rejoin at 183.
 * All paths inert unless TX8 explicit triple RF compilation gates.
 * Data only; physical TX requires guarded one-shot bench. MIT license. */
#ifndef FLUX_TX8_STEER_PHASE_PROBE_H
#define FLUX_TX8_STEER_PHASE_PROBE_H
#define TX8_NS 192u
#define TX8_PILOT_SLOTS 72u
#define TX8_TAIL_GUARD 6u
#define TX8_WORD_COUNT 8u
static const uint16_t tx8_words[TX8_WORD_COUNT] = {
  5183u,8074u,11264u,14702u,17479u,20515u,23467u,26007u
};
static const uint8_t tx8_symbols[TX8_NS] = {
  0,0,0,7,7,7,1,1,1,6,6,6,
  2,2,2,5,5,5,6,6,6,1,1,1,
  7,7,7,3,3,3,4,4,4,0,0,0,
  7,7,7,0,0,0,6,6,6,2,2,2,
  0,0,0,7,7,7,1,1,1,6,6,6,
  2,2,2,5,5,5,4,4,4,0,0,0,
  7,7,7,0,0,0,6,6,6,2,2,2,
  5,5,5,1,1,1,6,6,6,1,1,1,
  0,0,0,7,7,7,1,1,1,6,6,6,
  2,2,2,5,5,5,6,6,6,2,2,2,
  5,5,5,1,1,1,6,6,6,1,1,1,
  7,7,7,3,3,3,4,4,4,0,0,0,
  7,7,7,0,0,0,6,6,6,2,2,2,
  0,0,0,7,7,7,1,1,1,6,6,6,
  2,2,2,5,5,5,4,4,4,1,1,1,
  2,2,2,0,0,0,0,0,0,0,0,0,
};
#endif
