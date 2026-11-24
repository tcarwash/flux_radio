/* Offline-only register fixture. Not a deployable RF module. SPDX-License-Identifier: MIT */
#define K6_MODULE_LEVEL 2
#include "../../../c/k6/k6_mod.h"
int k6_main(const k6_api_t *a) {
  uint32_t size, *s = a->scratch(&size);
  if (!s || size < 16) return -1;
  uint32_t count=s[0], n=s[1], gap=s[2], lead=s[3];
  if (!count || n<2 || n>128 || (n&1) || count>64 || 16+count*n>size) return -2;
  const uint16_t *data=(const uint16_t *)(s+4);
  a->fd_write(0x3f,0x8000);
  a->fd_write(0x58,0xe1);
  a->fd_write(0x72,0xc194);
  a->fd_write(0x5a,0x076b);
  a->fd_write(0x5b,0x3e36);
  a->fd_write(0x5c,0x5625);
  a->fd_write(0x5d,(n-1)<<8);
  a->delay_us(lead);
  for (uint32_t f=0;f<count;f++) {
    a->fd_write(0x59,0x00f8);
    a->fd_write(0x02,0);
    a->fd_write(0x59,0x80f8);
    a->fd_write(0x59,0x00f8);
    for (uint32_t i=0;i<n/2;i++) a->fd_write(0x5f,*data++);
    a->fd_write(0x59,0x08f8);
    uint32_t start=a->ms();
    while (!(a->fd_read(0x0c)&1)) {
      if (a->ms()-start>1000) {
        a->fd_write(0x59,0x00f8);a->fd_write(0x3f,0);return -3;
      }
      a->delay_us(500);
    }
    a->fd_write(0x59,0x00f8);
    a->delay_us(gap);
  }
  a->fd_write(0x3f,0);
  return (int)count;
}
