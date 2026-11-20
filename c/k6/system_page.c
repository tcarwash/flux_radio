/* SPDX-License-Identifier: MIT */
#include "system_page.h"
static unsigned word(const uint8_t *p) {
  return (unsigned)p[0] | (unsigned)p[1]<<8 | (unsigned)p[2]<<16 | (unsigned)p[3]<<24;
}
int ks_validate(unsigned slot, uint8_t *dst, unsigned total,
                unsigned id, unsigned functions, const uint8_t build[8]) {
  unsigned n, i, table, base;
  if (slot!=0 || !dst || ((uintptr_t)dst&3) || !build || id==0 || id>255 || !functions || functions>255 || total<KM_HDR+KM_TAG || total>KL_SLOT_BYTES) return KL_BAD_HDR;
  n=total-KM_HDR-KM_TAG; table=8+4*functions;
  if (n<table+2+8) return KL_BAD_HDR;
  if (word(dst)!=(0x0053364bu | (unsigned)KM_ABI<<24) || (dst[4]|(unsigned)dst[5]<<8)!=n || dst[6] || dst[7] || dst[12]>KM_LEVEL || dst[13]!=(KM_F_LIB|KM_F_PAGE) || dst[14] || dst[15]!=slot) return KL_BAD_HDR;
  if (km_tag_check(dst,n)) return KL_BAD_TAG;
  dst+=KM_HDR; base=(unsigned)(uintptr_t)dst;
  if (word(dst)!=(0x504bu|id<<16|functions<<24) || word(dst+4)!=base) return KL_BAD_HDR;
  for(i=0;i<8;i++) if(dst[n-8+i]!=build[i]) return KL_BAD_HDR;
  for(i=0;i<functions;i++) {
    unsigned f=word(dst+8+4*i);
    if (!(f&1) || f-1<base+table || f-1>base+n-10) return KL_BAD_HDR;
  }
  return (int)n;
}

/* Directory stores absolute SPI address and image length. Erased entries fail bounds. */
int ks_entry(const uint8_t h[12],const uint8_t e[8],const uint8_t build[8],unsigned cap){
  unsigned addr=word(e),n=word(e+4);
  if(word(h)!=KS_DIRECTORY_MAGIC || word(h+4)!=word(build) || word(h+8)!=word(build+4))return KL_BAD_HDR;
  if(addr<KS_PAYLOAD || addr>=KS_END || (addr&3) || n<46 || n>cap || n>KS_END-addr)return KL_BAD_HDR;
  return (int)n;
}
int ks_load(unsigned slot,const kl_flash_t *fl,uint8_t *dst,unsigned cap,unsigned id,unsigned functions,const uint8_t build[8]){
  uint8_t h[12],e[8];int n;
  if(slot!=0 || !id || id>KS_MAX_ID || !fl || !fl->read || !dst || !build)return KL_BAD_HDR;
  if(fl->read(KS_BASE,h,12)||fl->read(KS_BASE+16+8*id,e,8))return KL_IO;
  n=ks_entry(h,e,build,cap);if(n<0)return n;
  if(fl->read(word(e),dst,(unsigned)n))return KL_IO;
  return ks_validate(0,dst,(unsigned)n,id,functions,build);
}
