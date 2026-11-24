/* Cortex-M0 module to qualify actual API28 codebook/IVF deck callbacks
 * through Aura's real module dispatcher and simulated SPI flash. No TX.
 * SPDX-License-Identifier: MIT */
#define K6_MODULE_LEVEL 31
#include "../../../c/k6/k6_mod.h"
int k6_main(const k6_api_t *a) {
    uint32_t room=0;
    uint8_t *p;
    if (!a || a->abi!=1 || a->level!=31) return -2;
    p=(uint8_t *)a->scratch(&room);
    if(!p || room<512u || ((uintptr_t)p&3u)) return -3;
    if(a->resource_read(1,0,p,16)!=-1) return -10; /* unopened deck */
    if(a->resource_open(32)!=-1) return -11;        /* invalid slot */
    if(a->resource_read(0,0,p,32)!=0) return -12;   /* actual SPI K6CB */
    if(p[0]!='K'||p[1]!='6'||p[2]!='C'||p[3]!='B'
       ||p[4]!=1||p[6]!=20) return -13;
    if(a->resource_read(0,32,p,20)!=0) return -14;
    if(a->resource_read(0,30750,p,2)!=0) return -15;
    if(a->resource_read(0,30751,p,2)!=-1) return -16;
    if(a->resource_read(0,0,p,257)!=-1) return -17;
    if(a->resource_read(0,0,(uint8_t *)0,1)!=-1) return -18;
    if(a->resource_read(0,0,(uint8_t *)0x40000000,1)!=-1) return -19;
    if(a->resource_open(14)!=4182) return -20;     /* KUL2/legacy library */
    if(a->resource_read(1,0,p,16)!=0) return -21;
    if(p[0]!='K'||p[1]!='6'||p[2]!='S'||p[3]!='C'||p[4]!=1) return -22;
    if(a->resource_read(1,4096,p,86)!=0) return -23;
    if(a->resource_read(1,4181,p,1)!=0) return -24;
    if(a->resource_read(1,4182,p,1)!=-1) return -25;
    if(a->resource_read(2,0,p,1)!=-1) return -26;
    if(a->resource_open(13)!=-1) return -27;       /* invalid deck clears handle */
    if(a->resource_read(1,0,p,4)!=-1) return -28;
    if(a->resource_open(14)!=4182) return -29;
    if(a->resource_read(1,16,p,128)!=0) return -30;
    return 7;
}
