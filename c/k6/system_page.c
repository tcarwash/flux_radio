/* SPDX-License-Identifier: MIT */
#include "system_page.h"
static unsigned word(const uint8_t *p) {
  return (unsigned)p[0] | (unsigned)p[1]<<8 | (unsigned)p[2]<<16 | (unsigned)p[3]<<24;
}
int ks_load(unsigned slot, const kl_flash_t *fl, uint8_t *dst, unsigned cap,
            unsigned id, unsigned functions, const uint8_t build[8]) {
  unsigned n, total, i, table, base;
  uint32_t addr;
  if (slot>=KS_SLOTS || !fl || !fl->read || !dst || !build || id==0 || id>255 || !functions || functions>255 || cap<KM_HDR) return KL_BAD_HDR;
  addr=KS_BASE+slot*KL_SLOT_BYTES;
  if (fl->read(addr,dst,KM_HDR)) return KL_IO;
  n=dst[4]|(unsigned)dst[5]<<8; total=KM_HDR+n+KM_TAG; table=8+4*functions;
  if (total>cap || total>KL_SLOT_BYTES || n<table+2+8) return KL_BAD_HDR;
  if (fl->read(addr,dst,total)) return KL_IO;
  if (word(dst)!=(0x004d364bu | (unsigned)KM_ABI<<24) || (dst[4]|(unsigned)dst[5]<<8)!=n || dst[6] || dst[7] || dst[12]>KM_LEVEL || dst[13]!=(KM_F_LIB|KM_F_PAGE) || dst[14] || dst[15]!=slot) return KL_BAD_HDR;
  if (km_tag_check(dst,n)) return KL_BAD_TAG;
  dst+=KM_HDR; base=(unsigned)(uintptr_t)dst;
  if (word(dst)!=(0x504bu|id<<16|functions<<24) || word(dst+4)!=base) return KL_BAD_HDR;
  for(i=0;i<8;i++) if(dst[n-8+i]!=build[i]) return KL_BAD_HDR;
  for(i=0;i<functions;i++) {
    unsigned f=word(dst+8+4*i);
    if (!(f&1) || f-1<base+table || f-1>=base+n-8) return KL_BAD_HDR;
  }
  return (int)n;
}
