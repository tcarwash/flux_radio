/* Emulator-only native 4800 2FSK polling fixture. No TX, no expected payload.
 * Scratch: deadline ms, output count, frame bytes, FIFO threshold, poll us, then raw frames.
 * SPDX-License-Identifier: MIT */
#define K6_MODULE_LEVEL 2
#include "../../../c/k6/k6_mod.h"
static void arm(const k6_api_t *a) {
  a->fd_write(0x59, 0x40f8);
  a->fd_write(0x59, 0x10f8);
  a->fd_write(0x02, 0);
  a->fd_write(0x3f, 0x3002);
}
int k6_main(const k6_api_t *a) {
  uint32_t size, *s = a->scratch(&size);
  if (!s || size < 22) return -1;
  uint32_t deadline = s[0], count = 0, got = 0;
  uint32_t n=s[2], threshold=s[3], poll=s[4];
  if (n<2 || n>256 || (n&1) || threshold<1 || threshold>7 || !poll) return -2;
  uint32_t words=n/2;
  uint16_t *data = (uint16_t *)(s+5);
  s[1] = 0;
  a->fd_write(0x58, 0x00e1);
  a->fd_write(0x72, 0xc194);
  a->fd_write(0x5a, 0x076b);
  a->fd_write(0x5b, 0x3e36);
  a->fd_write(0x5c, 0x5625);
  a->fd_write(0x5d, (n-1)<<8);
  a->fd_write(0x5e, 0x0200|threshold);
  arm(a);
  while (a->ms() < deadline && 20+(count+1)*n <= size) {
    if (a->fd_read(0x0c)&1) {
      a->fd_write(0x02, 0);
      uint16_t f = a->fd_read(0x02);
      if (f&0x1000)
        for (uint32_t i=0;i<threshold && got<words;i++) data[count*words+got++] = a->fd_read(0x5f);
      if (f&0x2000) {
        while (got<words) data[count*words+got++] = a->fd_read(0x5f);
        s[1] = ++count;
        got = 0;
        arm(a);
      }
    }
    a->delay_us(poll);
  }
  a->fd_write(0x59, 0x00f8);
  a->fd_write(0x3f, 0);
  return (int)count;
}
