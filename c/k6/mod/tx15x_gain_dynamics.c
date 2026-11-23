/* TX-15X: timed REG70 gain modulation on a fixed REG71 FM tone.
 * Three 24-symbol alternating 16/64-gain blocks at 50, 100 and ~149.25
 * symbols/s, bracketed by gain-24 pilot plateaus. REG71 stays fixed.
 * Guarded, short, conducted dummy-load experiment; inert by default.
 * SPDX-License-Identifier: MIT.
 */
#define K6_MODULE_LEVEL 3
#include "../k6_mod.h"
#ifndef TX15X_BENCH_ENABLE
#define TX15X_BENCH_ENABLE 0
#endif
#ifndef TX15X_RF_ENABLE
#define TX15X_RF_ENABLE 0
#endif
#ifndef TX15X_FIXTURE_ACK
#define TX15X_FIXTURE_ACK 0
#endif
#ifndef TX15X_WATCHDOG_ACK
#define TX15X_WATCHDOG_ACK 0
#endif
#ifndef TX15X_HZ
#define TX15X_HZ 433500000u
#endif
#if TX15X_RF_ENABLE && (!TX15X_BENCH_ENABLE || !TX15X_FIXTURE_ACK || !TX15X_WATCHDOG_ACK)
#error "TX15X requires explicit bench/fixture/watchdog RF ACKs"
#endif
#if TX15X_HZ != 433500000u
#error "TX15X fixed 433500000Hz conducted bench only"
#endif
#define X_LEASE_MS 1800u
#define X_TONE_WORD 11264u
#define X_SYMBOLS 24u
#define X_PILOT_TICKS 1000u
#define X_LEAD_US 60000u
#define X_TAIL_US 80000u
#if TX15X_BENCH_ENABLE
static int write_gain(const k6_api_t *api, uint8_t gain) {
  uint16_t v=(uint16_t)(0x8000u|((uint16_t)gain<<8));
  if(api->fd_write(0x70,v)!=0)return -1;
  return api->fd_read(0x70)==v?0:-1;
}
static int wait_ticks(const k6_api_t *api, uint16_t start, uint16_t target) {
  while((uint16_t)((uint16_t)api->ticks()-start)<target) {
    if(api->key_down())return -1;
    api->delay_us(100u);
  }
  return 0;
}
static int run_rate(const k6_api_t *api, uint16_t period_ticks) {
  uint16_t start=(uint16_t)api->ticks();
  uint16_t i;
  for(i=0;i<X_SYMBOLS;i++) {
    if(write_gain(api,(i&1u)?64u:16u)!=0)return -1;
    if(wait_ticks(api,start,(uint16_t)((i+1u)*period_ticks))!=0)return -2;
  }
  return 0;
}
#endif
int k6_main(const k6_api_t *api) {
  if(api->abi!=1||api->level<3||api->fd_read(0)!=0x4829u)return -3;
#if !TX15X_BENCH_ENABLE
  api->say("tx15x disabled; explicit bench wrapper required");
  return -9;
#else
  uint16_t old70=0,old71=0;
  int ret=0,keyed=0,snapshot=0,restored=1;
  if(api->rx_hz()!=TX15X_HZ||api->key_down())return -2;
  if(KM_RSSI_DBM(api->fd_read(0x67))>-90)return -7;
  api->delay_us(50000u);
  if(KM_RSSI_DBM(api->fd_read(0x67))>-90)return -7;
  if(api->tx_on(X_LEASE_MS)!=0)return -5;
  keyed=1;
  if(api->tx_pa(0)!=0){ret=-12;goto done;}
  old70=api->fd_read(0x70); old71=api->fd_read(0x71); snapshot=1;
  if(api->fd_write(0x70,0)!=0||api->fd_write(0x71,X_TONE_WORD)!=0){ret=-12;goto done;}
  api->delay_us(X_LEAD_US);
  if(write_gain(api,24)!=0){ret=-14;goto done;}
#if TX15X_RF_ENABLE
  if(api->tx_pa(1)!=0){ret=-12;goto done;}
#endif
  api->delay_us(100000u);
  if(run_rate(api,200u)!=0||run_rate(api,100u)!=0||run_rate(api,67u)!=0){ret=-4;goto done;}
  if(write_gain(api,24)!=0){ret=-14;goto done;}
  api->delay_us(100000u);
  ret=3;
done:
  if(keyed) {
    (void)api->tx_pa(0);
    if(snapshot) {
      if(api->fd_write(0x70,old70)!=0)restored=0;
      if(api->fd_write(0x71,old71)!=0)restored=0;
      if(api->fd_read(0x70)!=old70||api->fd_read(0x71)!=old71)restored=0;
    }
    api->tx_off();
  }
  if(!restored)return -16;
  api->say("tx15x 50/100/149.25sps 16/64 restore=ok");
  return ret;
#endif
}
