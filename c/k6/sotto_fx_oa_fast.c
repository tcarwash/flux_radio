/* Flux-specific speed specialization of Sotto's fx_oa_block.
 *
 * Exact arithmetic copied from pinned MIT Sotto c/fixed/fx_math.c's
 * FX_NARROW_MUL implementation, compiled at -O2 while all other
 * fixed DSP math stays -Oz to fit the FD6818B firmware flash.
 * This object is inert without the normal Sotto decoder caller.
 */
#include "fixed/fx.h"
#include "fixed/fx_tables.h"

#ifndef FX_NARROW_MUL
#error "Sotto OA specialization requires FX_NARROW_MUL"
#endif

void fx_oa_block(const int16_t *bin, const int16_t *re, const int16_t *im,
                 int nb, int n0, int ns, fx_acc *a) {
    int i, j;
    for (i = 0; i < ns; i++) a[i] = 0;
    for (j = 0; j < nb; j++) {
        unsigned step = (unsigned)bin[j] & 511u;
        unsigned x = ((unsigned)bin[j] * (unsigned)n0) & 511u;
        if ((j & 7) == 7) FX_YIELD();
        int32_t r = re[j], m = im[j];
#ifdef FX_OA_PACKED
        const uint32_t *cs = fx_cs512();
        for (i = 0; i < ns; i++) {
            uint32_t w = cs[x];
            a[i] += r * (int16_t)w - m * ((int32_t)w >> 16);
            x = (x + step) & 511u;
        }
#else
        for (i = 0; i < ns; i++) {
            a[i] += r * fx_cos512[x] - m * fx_cos512[(x - 128) & 511u];
            x = (x + step) & 511u;
        }
#endif
    }
}
