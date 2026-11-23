/* TX-24 REG40 deviation low-byte scan (FD6818B / B-chip).
 *
 * HYPOTHESIS ONLY: REG40 low byte may scale FM deviation from an
 * unchanged REG71 audio oscillator (1091 Hz, fixed REG70 gain24).
 * Higher byte (0x35) and all PLL, APC and FSK registers stay fixed.
 * The old-family BK4819 bitfield law is NOT presumed valid for 0x4829.
 * If REG40 is latched, the conducted RF will reveal a negative.
 *
 * Defaults inert, no radio or SPI access until separately operator
 * approved and source-ACK enabled. PA0 still leaks RF: use dummy load.
 * A GitHub queue CLAIM is not a radio lease; a separate one-shot bench
 * harness must acquire canonical nonblocking Flux port lock first.
 * SPDX-License-Identifier: MIT
 */
#define K6_MODULE_LEVEL 3
#include "../k6_mod.h"

#ifndef TX24_BENCH_ENABLE
#define TX24_BENCH_ENABLE 0
#endif
#ifndef TX24_RF_ENABLE
#define TX24_RF_ENABLE 0
#endif
#ifndef TX24_FIXTURE_ACK
#define TX24_FIXTURE_ACK 0
#endif
#ifndef TX24_WATCHDOG_ACK
#define TX24_WATCHDOG_ACK 0
#endif
#ifndef TX24_FREQ_HZ
#define TX24_FREQ_HZ 433500000u
#endif
#if TX24_RF_ENABLE && (!TX24_BENCH_ENABLE || !TX24_FIXTURE_ACK || !TX24_WATCHDOG_ACK)
#error "TX24 RF requires enabled bench, verified dummy-load fixture and independent watchdog"
#endif
#if TX24_FREQ_HZ != 433500000u
#error "TX24 cannot use a different radio frequency: hardcoded conducted fixture only"
#endif
#define TX24_ID 0x4829u
#define TX24_REG40 0x40u
#define TX24_REG70 0x70u
#define TX24_REG71 0x71u
#define TX24_REG_STATE 0x30u
#define TX24_REG_RSSI 0x67u
#define TX24_REG40_BASE 0x3516u
#define TX24_TONE_FREQ_WORD 11264u
#define TX24_GAIN24 0x9800u
#define TX24_LEASE_MS 1800u
#define TX24_LEAD_US 80000u
#define TX24_STEP_US 90000u
#define TX24_TAIL_US 85000u
#define TX24_SLOTS 13u

#if TX24_BENCH_ENABLE
/* The high byte remains 0x35 in every step. Only low8 varies:
 * reference 0x16 is repeatedly alternated with 0x00,0x40,0x80.
 * No second register is updated during any scoring segment.
 * Baseline pilot 0 and 2; all later references remain independent.
 */
static const uint8_t TX24_LOW8[TX24_SLOTS]={
  0x16u,0x40u,0x16u,0x80u,0x16u,0x00u,0x16u,
  0x40u,0x16u,0x80u,0x16u,0x00u,0x16u
};
static char *put(char *p,const char *s) {
  while(*s)*p++=*s++;
  return p;
}
static char *dec(char *p,uint32_t n) {
  static const uint32_t pw[]={
    1000000000u,100000000u,10000000u,1000000u,100000u,
    10000u,1000u,100u,10u,1u};
  uint32_t i,lead=0;
  for(i=0;i<10;i++){
    uint32_t d=0;while(n>=pw[i]){n-=pw[i];d++;}
    if(d||lead||i==9){*p++=(char)('0'+d);lead=1;}
  }
  return p;
}
static int write_check(const k6_api_t *api,uint8_t reg,uint16_t value) {
  if(api->fd_write(reg,value)!=0)return -12;
  return api->fd_read(reg)==value?0:-14;
}
#endif

int k6_main(const k6_api_t *api) {
  if(!api||api->abi!=1||api->level<3||api->fd_read(0u)!=TX24_ID)return -3;
#if !TX24_BENCH_ENABLE
  api->say("tx24 inert: offline source only");return -9;
#else
  uint16_t saved40=0,saved70=0,saved71=0;
  uint32_t i;
  char line[60],*p;
  int keyed=0,snapshot=0,ret=0,restore_good=1;
  if(api->rx_hz()!=TX24_FREQ_HZ||api->key_down() ||
     (api->fd_read(TX24_REG_STATE)&2u))return -2;
  if(KM_RSSI_DBM(api->fd_read(TX24_REG_RSSI))>-90)return -7;
  api->delay_us(50000u);
  if(api->key_down() ||
     KM_RSSI_DBM(api->fd_read(TX24_REG_RSSI))>-90)return -7;
  if(api->tx_on(TX24_LEASE_MS)!=0)return -5;
  keyed=1;
  if(api->tx_pa(0)!=0){ret=-12;goto done;}
  saved40=api->fd_read(TX24_REG40);
  saved70=api->fd_read(TX24_REG70);
  saved71=api->fd_read(TX24_REG71);
  /* Do not silently force unfamiliar firmware's deviation state.
   * Nothing has been written to REG40/70/71 on this error path.
   */
  if(saved40!=TX24_REG40_BASE){ret=-17;goto done;}
  snapshot=1;
  if((ret=write_check(api,TX24_REG70,0u))!=0)goto done;
  if((ret=write_check(api,TX24_REG71,TX24_TONE_FREQ_WORD))!=0)goto done;
  api->delay_us(TX24_LEAD_US);
  if((ret=write_check(api,TX24_REG70,TX24_GAIN24))!=0)goto done;
#if TX24_RF_ENABLE
  if(api->tx_pa(1)!=0){ret=-12;goto done;}
#endif
  for(i=0;i<TX24_SLOTS;i++){
    uint16_t word=(uint16_t)((TX24_REG40_BASE&0xff00u)|TX24_LOW8[i]);
    if(api->key_down()){ret=-4;goto done;}
    if((ret=write_check(api,TX24_REG40,word))!=0)goto done;
    api->delay_us(TX24_STEP_US);
  }
  if((ret=write_check(api,TX24_REG70,0u))!=0)goto done;
  api->delay_us(TX24_TAIL_US);
  ret=(int)TX24_SLOTS;
done:
  if(keyed){
    (void)api->tx_pa(0);
    if(snapshot){
      if(api->fd_write(TX24_REG70,0u)!=0)restore_good=0;
      if(api->fd_write(TX24_REG40,saved40)!=0)restore_good=0;
      if(api->fd_write(TX24_REG71,saved71)!=0)restore_good=0;
      if(api->fd_write(TX24_REG70,saved70)!=0)restore_good=0;
      if(api->fd_read(TX24_REG40)!=saved40 ||
         api->fd_read(TX24_REG70)!=saved70 ||
         api->fd_read(TX24_REG71)!=saved71)restore_good=0;
    }
    api->tx_off();
  }
  if(!restore_good)return -16;
  p=put(line,"tx24 n=");p=dec(p,ret<0?0:(uint32_t)ret);
  p=put(p," pa=");p=dec(p,TX24_RF_ENABLE);
  *p=0;api->say(line);
  return ret;
#endif
}
