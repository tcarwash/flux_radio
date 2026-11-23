/* Read-only SPI-backed slim-codec table lookups.
 *
 * The V1 K6SC deck layout is byte-identical to the upstream fmsotto
 * 90-second live RX->Sotto encoder. Callback owns all radio SPI reads
 * and is configured ONLY after verifying signed slot origin.
 *
 * No .bss or heap. dv_flash_active() must return a persistent context
 * from the caller's scratch/relocated-vector storage.
 * SPDX-License-Identifier: MIT
 */
#include <stdint.h>
#include "dv_live_slim_spi.h"

#define CENTROIDS (64u+64u+32u)
#define DECK_OFF (DV_FLASH_DECK_HEADER + CENTROIDS*DV_FLASH_IVF_CD)
#define DECK_VEC (DECK_OFF + 2u*(CENTROIDS+3u))
#define DECK_END (DECK_VEC+2u*(512u+512u+256u))

static void zero_bytes(void *ptr,unsigned n) {
    uint8_t *d=(uint8_t *)ptr;
    unsigned i;
    for(i=0;i<n;i++)d[i]=0u;
}
static int read_at(uint32_t a,void *b,unsigned n) {
    dv_flash_ctx_t *s=dv_flash_active();
    if(!s || s->fault || !s->read || s->read(a,b,n,s->ctx)!=0) {
        if(s)s->fault=1u;
        zero_bytes(b,n);
        return -1;
    }
    return 0;
}
int dv_flash_fault(void) {
    dv_flash_ctx_t *s=dv_flash_active();
    return !s || s->fault!=0u;
}
int dv_flash_configure(dv_flash_read_cb read,void *ctx,uint32_t deck_address) {
    dv_flash_ctx_t *s=dv_flash_active();
    uint8_t hdr[16],buf[128];
    uint32_t expected,sum=0u;
    unsigned off;
    if(!s)return -1;
    s->read=read;s->ctx=ctx;s->deck_addr=deck_address;s->fault=0u;
    if(!read || deck_address<0x110000u ||
       deck_address>0x200000u-DV_FLASH_DECK_SIZE ||
       DECK_END!=DV_FLASH_DECK_SIZE) {s->fault=1;return -1;}
    if(read_at(deck_address,hdr,sizeof(hdr))!=0)return -1;
    if(hdr[0]!='K' || hdr[1]!='6' || hdr[2]!='S' || hdr[3]!='C' ||
       hdr[4]!=1 || hdr[5]!=8 || hdr[6]!=64 || hdr[7]!=64 ||
       hdr[8]!=32 || hdr[9]!=26 || hdr[10]!=0 || hdr[11]!=0) {
        s->fault=1;return -1;
    }
    expected=(uint32_t)hdr[12]|(uint32_t)hdr[13]<<8|
             (uint32_t)hdr[14]<<16|(uint32_t)hdr[15]<<24;
    for(off=16;off<DV_FLASH_DECK_SIZE;off+=sizeof(buf)) {
        unsigned n=DV_FLASH_DECK_SIZE-off;
        unsigned i;
        if(n>sizeof(buf))n=sizeof(buf);
        if(read_at(deck_address+off,buf,n)!=0)return -1;
        for(i=0;i<n;i++)sum+=buf[i];
    }
    if(sum!=expected){s->fault=1;return -1;}
    return 0;
}
static unsigned first_cell(int stage) {
    return stage==0?0u:stage==1?64u:128u;
}
static unsigned cell_count(int stage) {
    return stage==0||stage==1?64u:32u;
}
const signed char *dv_flash_cb_fetch(int stage,int vector,signed char *buf) {
    static const unsigned base[4]={0u,512u,1024u,1280u};
    unsigned max;
    if(!buf)return 0;
    if(stage<0 || stage>3 || vector<0 ||
       vector>=(int)(max=(stage<2?512u:256u))) {
        dv_flash_ctx_t *s=dv_flash_active();
        if(s)s->fault=1u;
        zero_bytes(buf,20u);
        return buf;
    }
    (void)read_at(DV_FLASH_CB_BASE+(base[stage]+(unsigned)vector)*20u,buf,20u);
    return buf;
}
const signed char *dv_flash_ivf_centroid(int stage,int first,signed char *buf) {
    const unsigned n=16u*DV_FLASH_IVF_CD;
    dv_flash_ctx_t *s=dv_flash_active();
    if(!buf)return 0;
    if(!s){zero_bytes(buf,n);return buf;}
    if(stage<0 || stage>2 || first<0 ||
       (unsigned)first+16u>cell_count(stage)) {
        if(s)s->fault=1u;
        zero_bytes(buf,n);
        return buf;
    }
    (void)read_at(s->deck_addr+DV_FLASH_DECK_HEADER+
                  (first_cell(stage)+(unsigned)first)*DV_FLASH_IVF_CD,buf,n);
    return buf;
}
int dv_flash_ivf_cell(int stage,int cell,int16_t *values) {
    dv_flash_ctx_t *s=dv_flash_active();
    uint8_t ab[4];
    unsigned a,b,n;
    if(!s || !values || stage<0 || stage>2 || cell<0 ||
       (unsigned)cell>=cell_count(stage)) {
        if(s)s->fault=1u;
        return 0;
    }
    if(read_at(s->deck_addr+DECK_OFF+
               2u*(first_cell(stage)+(unsigned)stage+(unsigned)cell),ab,4u)!=0)return 0;
    a=(unsigned)ab[0]|((unsigned)ab[1]<<8);
    b=(unsigned)ab[2]|((unsigned)ab[3]<<8);
    if(b<a || b>1280u) {s->fault=1u;return 0;}
    n=b-a;
    if(n>DV_FLASH_IVF_MAXN) {s->fault=1u;return 0;}
    if(n && read_at(s->deck_addr+DECK_VEC+2u*a,values,2u*n)!=0)return 0;
    return (int)n;
}
