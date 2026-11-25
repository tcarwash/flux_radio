/* GP0 -> 8kHz PCM fractional boxcar decimator. Input is ONE population count
 * of the 64 DMA-captured PB1 bytes, each count in [0,64]. The DMA stream's
 * measured decimated output rate (~85-88kHz) is an argument, not a constant.
 * Streams across arbitrary packet/block boundaries; no malloc, FPU, libgcc,
 * flash writes or kernel/IRQ ownership. DOES NOT drive DMA itself.
 * Experimental: actual voice fidelity and full-duplex CPU feasibility TBA.
 * SPDX-License-Identifier: MIT */
#ifndef SOTTO_GP0_PCM_H
#define SOTTO_GP0_PCM_H
#include <stdint.h>
typedef struct {
 uint32_t fs;
 uint32_t phase;               /* [0,fs); units per high-rate sample: 8000 */
 int32_t area;                 /* weighted bit-density sum, bounded <~5.6e6 */
 int32_t dc;                   /* Q16 DC estimate, 1/128 smoothing per 8kHz output */
 uint32_t samples;
 uint32_t clipped;
} gp0_pcm_t;
/* Slow unsigned divide (~once per 8kHz output) avoids missing Cortex-M0 libgcc. */
static uint32_t gp0_pcm_udiv(uint32_t n,uint32_t d){
 uint32_t q=0,bit=1;
 if(!d)return 0;
 while(d<n && !(d&0x80000000u)){d<<=1;bit<<=1;}
 while(bit){if(n>=d){n-=d;q|=bit;}d>>=1;bit>>=1;}
 return q;
}
static void gp0_pcm_init(gp0_pcm_t*p,uint32_t fs){
 p->fs=fs;p->phase=0;p->area=0;p->dc=0;
 p->samples=0;p->clipped=0;
}
/* Return 1 when one PCM sample is emitted; otherwise 0.
 * 64-bit density bit count is decimated at calibrated fs, then centered,
 * low-passed by the average over the time covered by the output sample,
 * high-passed at roughly 62Hz to remove GP0 bias, scaled to signed 16-bit.
 */
static int gp0_pcm_push(gp0_pcm_t*p,unsigned bits,int16_t*out){
 if(!p||!out||p->fs<=16000||p->fs>160000||bits>64)return -1;
 int32_t x=2*(int32_t)bits-64;
 uint32_t newphase=p->phase+8000u;
 if(newphase<p->fs){p->area+=x*8000; p->phase=newphase;return 0;}
 uint32_t left=p->fs-p->phase;
 int32_t area=p->area+x*(int32_t)left;
 uint32_t a=(uint32_t)(area<0?-area:area);
 /* Q8 output amplitude: abs(area)*256 fits uint32 through 160kHz. */
 uint32_t q=gp0_pcm_udiv(a<<8,p->fs);
 int32_t centered=area<0?-(int32_t)q:(int32_t)q;
 /* Fractional precision prevents integer DC estimator stickiness at the
  * last 1/256 bin (which otherwise leaves ~2,040 PCM counts DC error). */
 p->dc+=((centered<<8)-p->dc)>>7;
 int32_t sample=(centered-(p->dc>>8))*8;
 if(sample>32767){sample=32767;p->clipped++;}
 if(sample< -32768){sample=-32768;p->clipped++;}
 *out=(int16_t)sample;
 p->phase=newphase-p->fs;
 p->area=x*(int32_t)p->phase;
 p->samples++;
 return 1;
}
#endif
