/* Program-memory-free DCT access for Sotto slim ARMv6-M.
 *
 * Ported directly from the upstream Blowfang fmsotto live MCU encoder's
 * proven cos-row construction. Replaces the 800-byte fx_dct Q15 table
 * with 21 Q15 magnitude constants and small integer arithmetic.
 * Does not alter the wire codec or radio hardware.
 *
 * Build the real Sotto c/fixed/sotto_fx_slim.c with
 *   -include c/k6/mod/dv_live_slim_dct.h
 * and compile only the statically needed tables using gc-sections.
 *
 * SPDX-License-Identifier: MIT
 */
#ifndef FLUX_DV_LIVE_SLIM_DCT_H
#define FLUX_DV_LIVE_SLIM_DCT_H
#include <stdint.h>

static inline int dv_live_dct_at(int j,int i) {
    static const int16_t q[21]={
        10362,10330,10235,10076,9855,9573,9233,8835,8383,7879,7327,
        6730,6091,5414,4704,3965,3202,2419,1621,813,0
    };
    int m=(2*i+1)*j;
    int sign=1;
    while(m>=80)m-=80;
    if(m>40)m=80-m;
    if(m>20){m=40-m;sign=-1;}
    return sign*q[m];
}
#define SLIM_DCT(j,i) dv_live_dct_at((j),(i))
#endif
