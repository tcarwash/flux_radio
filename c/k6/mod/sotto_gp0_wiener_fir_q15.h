/* Offline-designed 15-tap fixed mask for a GP0 speech-noise comparison.
 * Coefficients: FIR scale 1.5/floor .1, Q15, trained only against the
 * current statistical RFSim source. DC gain is retained (0.610); the receive
 * audio may be quieter than the unfiltered baseline. Experimental only.
 * SPDX-License-Identifier: MIT */
#ifndef FLUX_SOTTO_GP0_WIENER_FIR_Q15_H
#define FLUX_SOTTO_GP0_WIENER_FIR_Q15_H
#include <stdint.h>

typedef struct {
    int16_t history[16];
    uint8_t pos;
} gp0_wiener_fir_state_t;

static const int16_t gp0_wiener_fir_coeff_q15[15] = {
    -13, -87, -178, -18, 755, 2162, 3614, 7514,
    3614, 2162, 755, -18, -178, -87, -13
};

static inline int16_t gp0_wiener_fir_q15_sample(gp0_wiener_fir_state_t *s,
                                                 int16_t input) {
    int32_t acc = 0;
    unsigned i, p = s->pos;
    s->history[p] = input;
    i = p;
    for (unsigned k = 0; k < 15u; ++k) {
        acc += (int32_t)gp0_wiener_fir_coeff_q15[k] * s->history[i];
        i = (i - 1u) & 15u;
    }
    s->pos = (uint8_t)((p + 1u) & 15u);
    /* Coefficient L1 norm is < 0.65 in Q15, so full-scale input cannot clip. */
    acc = (acc + 16384) >> 15;
    return (int16_t)acc;
}

static inline void gp0_wiener_fir_q15_block(gp0_wiener_fir_state_t *s,
                                             int16_t *pcm, unsigned count) {
    for (unsigned n = 0; n < count; ++n)
        pcm[n] = gp0_wiener_fir_q15_sample(s, pcm[n]);
}
#endif
