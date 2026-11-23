/* TX8 PROBE: isolated FD6818B candidate tone-oscillator write-timing probe.
   The default binary is INERT. Only an explicitly compiled bench version runs
   under guarded TX. No blanket RF enable, no automatic retry, no real packet
   or implicit on-air station ID. Capture only with a suitable dummy load.
   SPDX-License-Identifier: MIT */
#define K6_MODULE_LEVEL 3
#include "../k6_mod.h"
#ifndef TX8_PHASE_VECTOR_V2
#define TX8_PHASE_VECTOR_V2 0
#endif
#if TX8_PHASE_VECTOR_V2
#include "tx8_phase_probe.h"
#else
#include "tx8_probe.h"
#endif

#ifndef TX8_BENCH_ENABLE
#define TX8_BENCH_ENABLE 0
#endif
#ifndef TX8_HZ
#define TX8_HZ 440850000u
#endif
#ifndef TX8_GAIN
#define TX8_GAIN 48u
#endif
/* Optional conducted-load RF calibration. Unlike the original
 * PA-off write-timing probe, this explicitly keys RF during the 64
 * tone events. The two independent acknowledgments must be supplied
 * by a wrapper outside the repository's inert/default build. */
#ifndef TX8_RF_ENABLE
#define TX8_RF_ENABLE 0
#endif
#ifndef TX8_RF_FIXTURE_ACK
#define TX8_RF_FIXTURE_ACK 0
#endif
#ifndef TX8_RF_WATCHDOG_ACK
#define TX8_RF_WATCHDOG_ACK 0
#endif
#ifndef TX8_SYMBOL_RATE
#define TX8_SYMBOL_RATE 600u
#endif
#if TX8_RF_ENABLE && (!TX8_BENCH_ENABLE || !TX8_RF_FIXTURE_ACK || !TX8_RF_WATCHDOG_ACK)
#error "TX8 RF needs enabled bench, dummy-load acknowledgement and independent watchdog"
#endif
#if TX8_RF_ENABLE && TX8_HZ != 433500000u
#error "TX8 RF research confined to the tested 433.500-MHz conducted fixture"
#endif
#if TX8_SYMBOL_RATE < 100u || TX8_SYMBOL_RATE > 600u
#error "TX8 RF symbol rate outside bounded test"
#endif
#if TX8_PHASE_VECTOR_V2 && TX8_SYMBOL_RATE != 150u
#error "TX-15P non-commensurate vector qualified only at 150 Hz schedule"
#endif

#define RCC_APB2ENR 0x40021018u
#define TIM14_CNT 0x40002024u
#define TIM17 0x40014800u
#define TIM17_CR1 (TIM17+0x00u)
#define TIM17_CNT (TIM17+0x24u)
#define TIM17_PSC (TIM17+0x28u)
#define TIM17_ARR (TIM17+0x2cu)
#define TIM17_EGR (TIM17+0x14u)
#define TIM17EN (1u << 18)
#define CYCLES_PER_SYMBOL (96000000u / TX8_SYMBOL_RATE)  /* 96-MHz MCU clock */
#define LEAD_US 100000u
#define EARLY_CYCLES 16000u       /* 167us ahead of expected symbol edge */
#define TX_LIMIT_MS (TX8_RF_ENABLE ? 1800u : 4000u)
#define TONE_ON ((uint16_t)(0x8000u|((TX8_GAIN&0x7fu)<<8)))

#if TX8_BENCH_ENABLE
static uint32_t rd(uint32_t addr) { return *(volatile uint32_t *)(uintptr_t)addr; }
static void wr(uint32_t addr,uint32_t val) { *(volatile uint32_t *)(uintptr_t)addr=val; }
static char *put(char *p, const char *s) { while (*s) *p++=*s++; return p; }
static char *dec(char *p, uint32_t v) {
  static const uint32_t places[] = {1000000000u,100000000u,10000000u,
    1000000u,100000u,10000u,1000u,100u,10u,1u};
  int i,started=0;
  for(i=0;i<10;i++){
    unsigned d=0;
    while(v>=places[i]) {v-=places[i];d++;}
    if(d||started||i==9){*p++=(char)('0'+d);started=1;}
  }
  return p;
}

typedef struct {
  uint32_t apb2, prev_cr1, prev_psc, prev_arr, prev_cnt;
  uint32_t t, c17, c14;
} clk_t;
static void clk_start(clk_t *c) {
  c->apb2=rd(RCC_APB2ENR);
  wr(RCC_APB2ENR,c->apb2|TIM17EN);
  c->prev_cr1=rd(TIM17_CR1);
  c->prev_psc=rd(TIM17_PSC);
  c->prev_arr=rd(TIM17_ARR);
  c->prev_cnt=rd(TIM17_CNT);
  wr(TIM17_CR1,0);
  wr(TIM17_PSC,0);
  wr(TIM17_ARR,0xffffu);
  wr(TIM17_EGR,1);
  wr(TIM17_CNT,0);
  wr(TIM17_CR1,1);
  c->t=0;
  c->c17=rd(TIM17_CNT)&0xffffu;
  c->c14=rd(TIM14_CNT)&0xffffu;
}
static uint32_t clk_now(clk_t *c) {
  uint32_t c17=rd(TIM17_CNT)&0xffffu, c14=rd(TIM14_CNT)&0xffffu;
  uint32_t coarse=((c14-c->c14)&0xffffu)*9600u;
  uint32_t fine=(c17-c->c17)&0xffffu;
  int32_t diff=(int32_t)(int16_t)(uint16_t)(fine-coarse);
  c->c17=c17;c->c14=c14;c->t+=coarse+(uint32_t)diff;
  return c->t;
}
static void clk_restore(clk_t *c) {
  wr(TIM17_CR1,0);
  wr(TIM17_PSC,c->prev_psc);
  wr(TIM17_ARR,c->prev_arr);
  wr(TIM17_CNT,c->prev_cnt);
  wr(TIM17_CR1,c->prev_cr1);
  wr(RCC_APB2ENR,c->apb2);
}
static uint32_t wait_cycle(clk_t *c,uint32_t target) {
  uint32_t now=clk_now(c);
  while((int32_t)(target-now)>0) {now=clk_now(c);}
  return now;
}
#endif

int k6_main(const k6_api_t *api) {
  if(api->abi!=1 || api->level<3) return -3;
  if(api->fd_read(0)!=0x4829u) return -6;
#if !TX8_BENCH_ENABLE
  api->say("tx8 disabled; bench build required");
  return -9;
#else
  char line[60],*p;
  clk_t clk;
  uint32_t i, late=0, worst=0, span;
  int ret=(int)TX8_NS;
  int keyed=0, clocked=0;
  uint16_t previous_tone=0, previous_gain=0;
  if(api->rx_hz()!=TX8_HZ){api->say("tx8 wrong frequency");return -2;}
  if(api->key_down()){api->say("tx8 key held");return -4;}
  if((rd(RCC_APB2ENR)&TIM17EN) && (rd(TIM17_CR1)&1u))
    return -11;  /* Do not steal a running timer. */
  /* Simple occupancy guard: this is NOT a complete CSMA implementation. */
  if(KM_RSSI_DBM(api->fd_read(0x67))>-90){
    api->say("tx8 channel occupied");return -7;
  }
  api->delay_us(50000u);
  if(KM_RSSI_DBM(api->fd_read(0x67))>-90) return -7;
  if(api->tx_on(TX_LIMIT_MS)!=0) return -5;
  keyed=1;
  (void)api->tx_pa(0);  /* PA OFF IS NOT ZERO RF: dummy-load bench only */
  previous_gain=api->fd_read(0x70);
  previous_tone=api->fd_read(0x71);
  api->delay_us(LEAD_US);
  if(api->key_down()){ret=-4;goto done;}
  if(api->fd_write(0x71,tx8_words[tx8_symbols[0]])!=0){ret=-12;goto done;}
  if(api->fd_write(0x70,TONE_ON)!=0){ret=-12;goto done;}
#if TX8_RF_ENABLE
  if(api->tx_pa(1)!=0){ret=-12;goto done;}
#endif
  clk_start(&clk);clocked=1;
  for(i=1;i<TX8_NS;i++){
    uint32_t target=i*CYCLES_PER_SYMBOL-EARLY_CYCLES;
    uint32_t now=wait_cycle(&clk,target);
    uint32_t dl=now>target ? now-target:0;
    if(dl>worst)worst=dl;
    if(dl>CYCLES_PER_SYMBOL/4u)late++;
    if(api->key_down()) {ret=-4;break;}
#if !TX8_RF_ENABLE
    if(api->tx_pa(0)<0) {ret=-4;break;}
#endif
    if(api->fd_write(0x71,tx8_words[tx8_symbols[i]])<0){ret=-12;break;}
  }
  if(ret==(int)TX8_NS) wait_cycle(&clk,TX8_NS*CYCLES_PER_SYMBOL);
  span=clk_now(&clk);
#if TX8_PHASE_VECTOR_V2
  p=put(line,"tx8p2 n=");
#else
  p=put(line,"tx8 n=");
#endif
  p=dec(p,TX8_NS);
  p=put(p," cycles=");p=dec(p,span);
  p=put(p," late=");p=dec(p,late);
  p=put(p," worst=");p=dec(p,worst);
  *p=0;
done:
  if(clocked)clk_restore(&clk);
  if(keyed){
    (void)api->tx_pa(0);
    (void)api->fd_write(0x70,previous_gain);
    (void)api->fd_write(0x71,previous_tone);
    api->tx_off();
  }
  if(ret==(int)TX8_NS) api->say(line);
  else api->say("tx8 aborted; TX released");
  return ret;
#endif
}
