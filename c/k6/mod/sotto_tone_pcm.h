/* Deterministic 1 kHz PCM at 8 kHz, 50% full scale.
 * Exactly 80 samples per 10 ms block, repeated with continuous phase.
 * Pure signal source, no GPIO, radio, serial or microphone access. */
#ifndef FLUX_SOTTO_TONE_PCM_H
#define FLUX_SOTTO_TONE_PCM_H
#include <stdint.h>
#define ST_TONE_BLOCKS 32u
#define ST_TONE_SAMPLES 80u
#define ST_TONE_SAMPLE_RATE 8000u
#define ST_TONE_HZ 1000u
static const int16_t st_sine8[8] = {
    0,11585,16384,11585,0,-11585,-16384,-11585
};
static inline int16_t st_tone_sample(unsigned sample){
    return st_sine8[sample & 7u];
}
#endif
