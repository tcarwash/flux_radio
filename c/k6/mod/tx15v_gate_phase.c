/* Flux TX-15V: B-chip REG_70 oscillator enable gating / phase memory.
 *
 * Hypotheses: while oscillator is disabled, internal phase
 * (a) keeps running, (b) freezes, or (c) restarts when re-enabled.
 * Four noncommensurate OFF durations (57,71,83,97 ms), 170-ms ON holds.
 * REG_71 never changed during the 1.25-s wave. PA remains keyed for
 * the whole bounded dummy-load test; all register changes read back.
 *
 * This is a separate source-injection entry point, not a serial MIC
 * input. RF-ON only with explicit source ACKs. Default source inert.
 * SPDX-License-Identifier: MIT
 */
#define K6_MODULE_LEVEL 3
#include "../k6_mod.h"
#ifndef TX15V_BENCH_ENABLE
#define TX15V_BENCH_ENABLE 0
#endif
#ifndef TX15V_RF_ENABLE
#define TX15V_RF_ENABLE 0
#endif
#ifndef TX15V_FIXTURE_ACK
#define TX15V_FIXTURE_ACK 0
#endif
#ifndef TX15V_WATCHDOG_ACK
#define TX15V_WATCHDOG_ACK 0
#endif
#ifndef TX15V_HZ
#define TX15V_HZ 433500000u
#endif
#if TX15V_RF_ENABLE && (!TX15V_BENCH_ENABLE || !TX15V_FIXTURE_ACK || !TX15V_WATCHDOG_ACK)
#error "TX15V RF requires bench, dummy-load ACK and independent watchdog"
#endif
#if TX15V_HZ != 433500000u
#error "TX15V cannot transmit outside existing 433.500MHz conducted bench"
#endif
#define LEASE_MS 1800u
#define AUDIO_WORD 11264u /* 1091-Hz serial-tone oscillator */
#define TONE_GAIN 24u
#define TONE_ON ((uint16_t)(0x8000u | (TONE_GAIN << 8)))
#define N_GATE 4u
#define INITIAL_ON_US 200000u
#define ON_US 170000u
#define LEAD_US 80000u
#define END_GUARD_US 100000u

#if TX15V_BENCH_ENABLE
static const uint32_t gap_us[N_GATE]={57000u,71000u,83000u,97000u};
static char *put(char *p,const char *s){while(*s)*p++=*s++;return p;}
static char *dec(char *p,uint32_t v) {
  static const uint32_t places[]={1000000000u,100000000u,10000000u,
    1000000u,100000u,10000u,1000u,100u,10u,1u};
  int i,started=0;for(i=0;i<10;i++){
    uint32_t d=0;while(v>=places[i]){v-=places[i];d++;}
    if(d||started||i==9){*p++=(char)('0'+d);started=1;}
  }
  return p;
}
#endif

int k6_main(const k6_api_t *api) {
  if(api->abi!=1||api->level<3||api->fd_read(0)!=0x4829u)return -3;
#if !TX15V_BENCH_ENABLE
  api->say("tx15v disabled; guarded bench build needed");return -9;
#else
  uint16_t saved_gain=0,saved_tone=0;
  int ret=0,keyed=0,snapshot=0,restored=1;
  uint32_t i;
  char line[60],*p;
  if(api->rx_hz()!=TX15V_HZ||api->key_down())return -2;
  if(KM_RSSI_DBM(api->fd_read(0x67))>-90)return -7;
  api->delay_us(50000);
  if(KM_RSSI_DBM(api->fd_read(0x67))>-90)return -7;
  if(api->tx_on(LEASE_MS)!=0)return -5;
  keyed=1;
  if(api->tx_pa(0)!=0){ret=-12;goto done;}
  saved_gain=api->fd_read(0x70);
  saved_tone=api->fd_read(0x71);
  snapshot=1;
  if(api->fd_write(0x70,0)!=0){ret=-12;goto done;}
  if(api->fd_write(0x71,AUDIO_WORD)!=0){ret=-12;goto done;}
  api->delay_us(LEAD_US);
  if(api->fd_write(0x70,TONE_ON)!=0){ret=-12;goto done;}
#if TX15V_RF_ENABLE
  if(api->tx_pa(1)!=0){ret=-12;goto done;}
#endif
  api->delay_us(INITIAL_ON_US);
  for(i=0;i<N_GATE;i++){
    if(api->key_down()){ret=-4;break;}
    if(api->fd_write(0x70,0)!=0){ret=-12;break;}
    if(api->fd_read(0x70)!=0){ret=-14;break;}
    api->delay_us(gap_us[i]);
    if(api->fd_write(0x70,TONE_ON)!=0){ret=-12;break;}
    if(api->fd_read(0x70)!=TONE_ON){ret=-14;break;}
    api->delay_us(ON_US);
  }
  if(!ret){
    if(api->fd_write(0x70,0)!=0){ret=-12;goto done;}
    if(api->fd_read(0x70)!=0){ret=-14;goto done;}
    api->delay_us(END_GUARD_US);
    ret=(int)(N_GATE*2u+1u);
  }
done:
  if(keyed){
    (void)api->tx_pa(0);
    if(snapshot){
      if(api->fd_write(0x70,saved_gain)!=0)restored=0;
      if(api->fd_write(0x71,saved_tone)!=0)restored=0;
      if(api->fd_read(0x70)!=saved_gain||api->fd_read(0x71)!=saved_tone)
        restored=0;
    }
    api->tx_off();
  }
  if(!restored)return -16;
  p=put(line,"tx15v n=");p=dec(p,ret<0?0:(uint32_t)ret);
  p=put(p," tone=");p=dec(p,AUDIO_WORD);
  p=put(p," pa=");p=dec(p,TX15V_RF_ENABLE);
  *p=0;api->say(line);
  return ret;
#endif
}
