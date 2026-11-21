/* pa11pcm: short receive-only PA11 audio stream for Flux FD6818B research.
   Samples the existing AF output without changing RF registers. No TX/PTT,
   SPI writes, loader changes or resident services. The 0x4819 board results
   are not assumed valid on 0x4829. The output is an indexed hex sample stream
   that the host checks for completeness before exporting WAV.
   Based on pa11adc.h (upstream MIT). SPDX-License-Identifier: MIT */
#define K6_MODULE_LEVEL 2
#include "../k6_mod.h"
#include "pa11adc.h"

#define GPIOA_MODER 0x48000000u
#define GPIOA_OSPEEDR 0x48000008u
#define GPIOA_ODR 0x48000014u
#define GPIOB_ODR 0x48000414u
#define GPIOB_BSRR 0x48000418u
#define GPIOB_BRR 0x48000428u
#define SPK (1u << 2)

#define N 1024u
#define W 90u
#define HOLD 3u
#define DROP 4096u
#define PER_LINE 12u

static uint32_t rd(uint32_t a) { return *(volatile uint32_t *)(uintptr_t)a; }
static void wr(uint32_t a, uint32_t v) { *(volatile uint32_t *)(uintptr_t)a = v; }
static char *put(char *p, const char *s) {
  while (*s) *p++ = *s++;
  return p;
}
static char *dec(char *p, uint32_t v) {
  static const uint32_t place[] = {1000000000u,100000000u,10000000u,1000000u,100000u,10000u,1000u,100u,10u,1u};
  int started = 0, i;
  for (i=0;i<10;i++) {
    int d = 0;
    while (v >= place[i]) v -= place[i], d++;
    if (d || started || i == 9) *p++ = (char)('0' + d), started = 1;
  }
  return p;
}
static char *hex4(char *p, uint32_t x) {
  static const char hex[] = "0123456789abcdef";
  *p++ = hex[(x >> 12) & 15u]; *p++ = hex[(x >> 8) & 15u];
  *p++ = hex[(x >> 4) & 15u]; *p++ = hex[x & 15u];
  return p;
}
int k6_main(const k6_api_t *api) {
  uint32_t moder, ospeed, odr, spk, m_in, room=0, i, j;
  uint32_t t0, t1, lo=0xffffu, hi=0;
  uint16_t *samples;
  pa11_t c={0,0,0,0};
  char line[64], *p;
  int ret=(int)N;
  if (api->abi != 1 || api->level < 2) return -3;
  samples=(uint16_t *)api->scratch(&room);
  if (!samples || room < N*2u) return -8;
  moder=rd(GPIOA_MODER);
  ospeed=rd(GPIOA_OSPEEDR);
  odr=rd(GPIOA_ODR);
  spk=rd(GPIOB_ODR)&SPK;
  m_in=moder & ~(3u << (2 * PA11_PIN));

  p=put(line,"pa11pcm n="); p=dec(p,N);
  p=put(p," w="); p=dec(p,W);
  p=put(p," h="); p=dec(p,HOLD);
  p=put(p," drop="); p=dec(p,DROP);
  *p=0; api->say(line);
  p=put(line,"pa11pcm r47="); p=hex4(p,api->fd_read(0x47));
  p=put(p," r48="); p=hex4(p,api->fd_read(0x48));
  *p=0; api->say(line);

  wr(GPIOA_OSPEEDR, ospeed | (3u << (2 * PA11_PIN)));
  wr(GPIOB_BRR, SPK);
  (void)pa11_sample(m_in,HOLD,W,&c);
  (void)pa11_sample(m_in,HOLD,W,&c);
  for (i=0;i<DROP;i++) (void)pa11_sample(m_in,HOLD,W,&c);
  t0=api->ticks();
  for (i=0;i<N;i++) {
    uint32_t s;
    if ((i & 63u)==0 && api->key_down()) { ret=-2; goto done; }
    s=pa11_sample(m_in,HOLD,W,&c);
    if (s > W*W) s=W*W;
    samples[i]=(uint16_t)s;
    if (s<lo) lo=s;
    if (s>hi) hi=s;
  }
  t1=api->ticks();
done:
  wr(GPIOA_ODR,odr);
  wr(GPIOA_MODER,moder);
  wr(GPIOA_OSPEEDR,ospeed);
  if (spk) wr(GPIOB_BSRR,SPK); else wr(GPIOB_BRR,SPK);
  if (ret<0) { api->say("pa11pcm aborted"); return ret; }

  p=put(line,"pa11pcm tick100us="); p=dec(p,(t1-t0)&0xffffu);
  p=put(p," lo="); p=dec(p,lo); p=put(p," hi="); p=dec(p,hi);
  *p=0; api->say(line);
  for (i=0;i<N;i+=PER_LINE) {
    p=put(line,"d "); p=hex4(p,i);
    for (j=0;j<PER_LINE && i+j<N;j++) p=hex4(p,samples[i+j]);
    *p=0; api->say(line);
    /* At 115200 baud, allow each frame to drain before queuing the next. */
    api->delay_us(8000);
  }
  api->say("pa11pcm end n=1024");
  return ret;
}
