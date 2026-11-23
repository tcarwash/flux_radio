/* TX-24B: isolate REG40 from fixed native-2FSK REG70 gain 21.
 * DO NOT use simultaneously with TX-26 per-symbol REG70 work.
 *
 * Default is INERT. Offline-only until a separately approved exclusive
 * queue/bench lease and canonical nonblocking Flux serial lock. The
 * source never writes REG40 DURING a frame and never touches in-flight
 * FIFO occupancy/refill. Eight 33-word finite frames: same 32-word
 * unwhitened known FSK pattern (long +/- runs), last word 0xA55A
 * sacrificial guard for the independently observed 0x00F8 leading word.
 *
 * This tests native-FSK coexistence, not 4FSK, phase IQ, 2 bits/symbol,
 * or symbol-rate REG40 updates. 433.500MHz conducted dummy load only.
 * SPDX-License-Identifier: MIT
 */
#define K6_MODULE_LEVEL 3
#include "../k6_mod.h"

#ifndef TX24B_BENCH_ENABLE
#define TX24B_BENCH_ENABLE 0
#endif
#ifndef TX24B_RF_ENABLE
#define TX24B_RF_ENABLE 0
#endif
#ifndef TX24B_FIXTURE_ACK
#define TX24B_FIXTURE_ACK 0
#endif
#ifndef TX24B_WATCHDOG_ACK
#define TX24B_WATCHDOG_ACK 0
#endif
#ifndef TX24B_HZ
#define TX24B_HZ 433500000u
#endif
#if TX24B_RF_ENABLE && (!TX24B_BENCH_ENABLE || !TX24B_FIXTURE_ACK || !TX24B_WATCHDOG_ACK)
#error "TX24B native FSK RF requires bench+dummy-load+independent watchdog"
#endif
#if TX24B_HZ != 433500000u
#error "TX24B fixed conducted fixture is 433.500MHz only"
#endif
#define TX24B_ID 0x4829u
#define TX24B_FRAME_COUNT 8u
#define TX24B_WORDS 33u
#define TX24B_GAIN_CODE 21u
#define TX24B_BAUD_WORD 0xc194u
#define TX24B_MAX_TX_MS 1900u
#define TX24B_FRAME_TIMEOUT_MS 700u
#define TX24B_REG40_ORIG 0x3516u
#define TX24B_R_CHIP 0x00u
#define TX24B_R_IRQ 0x02u
#define TX24B_R_STATE 0x30u
#define TX24B_R_MODE 0x3fu
#define TX24B_R_DEVIATION 0x40u
#define TX24B_R_PREAMBLE 0x58u
#define TX24B_R_CTRL 0x59u
#define TX24B_R_SYNC0 0x5au
#define TX24B_R_SYNC1 0x5bu
#define TX24B_R_SYNC2 0x5cu
#define TX24B_R_LENGTH 0x5du
#define TX24B_R_FIFO 0x5fu
#define TX24B_R_GAIN 0x70u
#define TX24B_R_BAUD 0x72u
#define TX24B_R_STATUS 0x0cu
#define TX24B_R_RSSI 0x67u

#if TX24B_BENCH_ENABLE
/* Never infer this ordering from the archive during scoring.
 * Frame 0 and frame 2 are the only time/frequency/reference pilots.
 * All other amplitudes are prospective holdout values.
 */
static const uint16_t deviation_words[TX24B_FRAME_COUNT] = {
  0x3516u,0x3540u,0x3516u,0x3580u,
  0x3516u,0x3500u,0x3540u,0x3580u
};
static const uint8_t snapshot_regs[] = {
  TX24B_R_MODE,TX24B_R_CTRL,TX24B_R_PREAMBLE,
  TX24B_R_GAIN,TX24B_R_BAUD,TX24B_R_SYNC0,
  TX24B_R_SYNC1,TX24B_R_SYNC2,TX24B_R_LENGTH,
  TX24B_R_DEVIATION
};
#define TX24B_SNAPSHOT_REGS (sizeof(snapshot_regs)/sizeof(snapshot_regs[0]))
static int putw(const k6_api_t *a,uint8_t r,uint16_t v) {
  return a->fd_write(r,v)==0 ? 0 : -13;
}
static int is_safe(const k6_api_t *a) {
  return !a->key_down() && !km_power_switch_off();
}
static int channel_clear(const k6_api_t *a) {
  return KM_RSSI_DBM(a->fd_read(TX24B_R_RSSI)) <= -90;
}
static uint16_t known_word(uint32_t i) {
  /* FSK level holds 64 bits = 4 consecutive 16-bit words
   * then switches sign for the next group, eight groups total:
   * [0,1,0,1,0,1,0,1] × 64 bits. Independent guard word
   * absorbs possible spurious leading 0x00F8 word.
   */
  if(i==32u)return 0xa55au;
  return ((i/4u)&1u) ? 0xffffu : 0u;
}
static int send_one(const k6_api_t *a,uint32_t frame) {
  uint32_t i,start;
  int r;
  /* Only change REG40 between completely stopped finite FIFO frames.
   * Never write REG70 or REG72 after source FSK initialization.
   */
  if((r=putw(a,TX24B_R_CTRL,0x00f8u))!=0)return r;
  if((r=putw(a,TX24B_R_IRQ,0u))!=0)return r;
  if((r=putw(a,TX24B_R_DEVIATION,deviation_words[frame]))!=0)return r;
  if(a->fd_read(TX24B_R_DEVIATION)!=deviation_words[frame])return -14;
  if((r=putw(a,TX24B_R_LENGTH,0x4100u))!=0)return r;
  if((r=putw(a,TX24B_R_CTRL,0x80f8u))!=0)return r;
  if((r=putw(a,TX24B_R_CTRL,0x00f8u))!=0)return r;
  for(i=0;i<TX24B_WORDS;i++) {
    if(!is_safe(a))return -4;
    if((r=putw(a,TX24B_R_FIFO,known_word(i)))!=0)return r;
  }
  if((r=putw(a,TX24B_R_CTRL,0x08f8u))!=0)return r;
  start=a->ms();
  for(;;) {
    if(!is_safe(a))return -4;
    if(a->fd_read(TX24B_R_STATUS)&1u)return (int)TX24B_WORDS;
    if((uint32_t)(a->ms()-start)>TX24B_FRAME_TIMEOUT_MS)return -15;
    a->delay_us(5000u);
  }
}
#endif

int k6_main(const k6_api_t *a) {
  if(!a||a->abi!=1||a->level<3||a->fd_read(TX24B_R_CHIP)!=TX24B_ID)return -3;
#if !TX24B_BENCH_ENABLE
  a->say("tx24b inert: REG40+FSK RF not enabled");
  return -9;
#else
  uint16_t old[TX24B_SNAPSHOT_REGS];
  uint32_t i,frame;
  int ret=0,keyed=0,restore=0;
  if(a->rx_hz()!=TX24B_HZ ||
     (a->fd_read(TX24B_R_STATE)&2u) ||
     !is_safe(a))return -2;
  if(!channel_clear(a))return -7;
  a->delay_us(50000u);
  if(!is_safe(a)||!channel_clear(a))return -7;
  if(a->fd_read(TX24B_R_MODE)&0xa000u)return -8;
  if(a->tx_on(TX24B_MAX_TX_MS)!=0)return -5;
  keyed=1;
  if(a->tx_pa(0)!=0){ret=-4;goto done;}
  for(i=0;i<TX24B_SNAPSHOT_REGS;i++)
    old[i]=a->fd_read(snapshot_regs[i]);
  if((old[0]&0xa000u)||(old[1]&0x0800u)||old[9]!=TX24B_REG40_ORIG){
    ret=-8;goto done;
  }
  restore=1;
#define WR(R,V) do {if(putw(a,(R),(uint16_t)(V))!=0){ret=-13;goto done;}} while(0)
  WR(TX24B_R_CTRL,0x00f8u);
  WR(TX24B_R_IRQ,0u);
  WR(TX24B_R_MODE,0x8000u);
  WR(TX24B_R_PREAMBLE,0x00e1u);
  WR(TX24B_R_GAIN,0x0080u|TX24B_GAIN_CODE);
  WR(TX24B_R_BAUD,TX24B_BAUD_WORD);
  WR(TX24B_R_SYNC0,0x076bu);
  WR(TX24B_R_SYNC1,0x3e36u);
  WR(TX24B_R_SYNC2,0x5625u);
  WR(TX24B_R_LENGTH,0x4100u);
#undef WR
#if TX24B_RF_ENABLE
  if(a->tx_pa(1)!=0){ret=-4;goto done;}
#endif
  for(frame=0;frame<TX24B_FRAME_COUNT;frame++) {
    ret=send_one(a,frame);
    if(ret!=(int)TX24B_WORDS)goto done;
  }
  ret=(int)(TX24B_FRAME_COUNT*TX24B_WORDS);
done:
  if(keyed) {
    (void)a->tx_pa(0);
    if(restore){
      if(putw(a,TX24B_R_CTRL,0x00f8u)!=0 ||
         putw(a,TX24B_R_MODE,0u)!=0 ||
         putw(a,TX24B_R_IRQ,0u)!=0)ret=-16;
      for(i=TX24B_SNAPSHOT_REGS;i>0;i--){
        uint32_t k=i-1u;
        if(putw(a,snapshot_regs[k],old[k])!=0)ret=-16;
      }
      for(i=0;i<TX24B_SNAPSHOT_REGS;i++){
        if(a->fd_read(snapshot_regs[i])!=old[i])ret=-16;
      }
    }
    a->tx_off();
  }
  if(ret==(int)(TX24B_FRAME_COUNT*TX24B_WORDS))
    a->say("tx24b: eight known 33-word FSK frames; restored");
  else
    a->say("tx24b abort: no retry; verify restore");
  return ret;
#endif
}
