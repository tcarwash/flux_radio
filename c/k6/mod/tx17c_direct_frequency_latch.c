/* TX-17C: direct frequency synthesizer low-word latch during real B-chip TX.
 * Diagnostic ONLY. 0x38 edits stay within +/- 100 x10Hz, low word only.
 * No RXON/TXON transition between steps, since that would hide whether
 * frequency words latch immediately. TX_ON is keyed only once, bounded 1.8s.
 * Original 0x38/0x39, 0x70/0x71 saved/readback-verified, all restored.
 * SPI library backing slot is always restored by the guarded host bench.
 *
 * Default build inert. Conducted RF requires three explicit defines.
 * This is not a general-frequency or on-air transmitter module.
 * SPDX-License-Identifier: MIT
 */
#define K6_MODULE_LEVEL 3
#include "../k6_mod.h"
#ifndef TX17_BENCH_ENABLE
#define TX17_BENCH_ENABLE 0
#endif
#ifndef TX17_RF_ENABLE
#define TX17_RF_ENABLE 0
#endif
#ifndef TX17_FIXTURE_ACK
#define TX17_FIXTURE_ACK 0
#endif
#ifndef TX17_WATCHDOG_ACK
#define TX17_WATCHDOG_ACK 0
#endif
#ifndef TX17_HZ
#define TX17_HZ 433500000u
#endif
#if TX17_RF_ENABLE && (!TX17_BENCH_ENABLE || !TX17_FIXTURE_ACK || !TX17_WATCHDOG_ACK)
#error "TX17C RF requires bench, load ACK and TX watchdog ACK"
#endif
#if TX17_HZ != 433500000u
#error "TX17C is constrained to 433.500 MHz existing dummy-load bench"
#endif
#define LEASE_MS 1800u
#define STEP_US 160000u
#define INTRO_US 150000u
#define TAIL_US 150000u
#define FREQWORD_LO 0x38u
#define FREQWORD_HI 0x39u
#define GAIN 0x70u
#define TONE 0x71u
#define NSTEP 6u
#if TX17_BENCH_ENABLE
static const int16_t dx[NSTEP]={0,50,0,-50,0,100}; /* +/- 500, 1000 Hz */

static char *put(char *p,const char *s){while(*s)*p++=*s++;return p;}
static char *dec(char *p,uint32_t v){
  static const uint32_t places[]={
    1000000000u,100000000u,10000000u,1000000u,
    100000u,10000u,1000u,100u,10u,1u};
  unsigned i;int started=0;
  for(i=0;i<10;i++){
    unsigned d=0;while(v>=places[i]){v-=places[i];d++;}
    if(d||started||i==9){*p++=(char)('0'+d);started=1;}
  }
  return p;
}
#endif
int k6_main(const k6_api_t *api){
  if(api->abi!=1||api->level<3||api->fd_read(0)!=0x4829u)return -3;
#if !TX17_BENCH_ENABLE
  api->say("tx17c disabled; explicit bench required");return -9;
#else
  int ret=0,keyed=0,snapshot=0,restore_ok=1;
  uint16_t rlo=0,rhi=0,rg=0,rt=0;
  uint32_t i;
  char line[60],*p;
  if(api->rx_hz()!=TX17_HZ||api->key_down())return -2;
  if(KM_RSSI_DBM(api->fd_read(0x67))>-90)return -7;
  api->delay_us(50000u);
  if(KM_RSSI_DBM(api->fd_read(0x67))>-90)return -7;
  if(api->tx_on(LEASE_MS)!=0)return -5;
  keyed=1;
  if(api->tx_pa(0)!=0){ret=-12;goto done;}
  rlo=api->fd_read(FREQWORD_LO);
  rhi=api->fd_read(FREQWORD_HI);
  rg=api->fd_read(GAIN);
  rt=api->fd_read(TONE);
  snapshot=1;
  /* Nominal 43350000 x10Hz=0x029577F0. Permit xtal calibration
   * correction in low word but refuse surprising synthesizer settings. */
  if(rhi!=0x0295u || rlo<0x7000u || rlo>0x8000u){
    ret=-10;goto done;
  }
  /* Force no internal tone, no mic: tx_on's mode selects mic off. */
  if(api->fd_write(GAIN,0)!=0){ret=-12;goto done;}
  if(api->fd_write(TONE,0)!=0){ret=-12;goto done;}
#if TX17_RF_ENABLE
  if(api->tx_pa(1)!=0){ret=-12;goto done;}
#endif
  api->delay_us(INTRO_US);
  for(i=0;i<NSTEP;i++){
    uint16_t next=(uint16_t)((int32_t)rlo+(int32_t)dx[i]);
    if(api->key_down()){ret=-4;break;}
    if(api->fd_write(FREQWORD_LO,next)!=0){ret=-13;break;}
    if(api->fd_read(FREQWORD_LO)!=next){ret=-14;break;}
    api->delay_us(STEP_US);
  }
  /* Bring to exact previous LO word and allow a final measured baseline. */
  if(!ret){
    if(api->fd_write(FREQWORD_LO,rlo)!=0){ret=-13;goto done;}
    if(api->fd_read(FREQWORD_LO)!=rlo){ret=-14;goto done;}
    api->delay_us(TAIL_US);
  }
  if(!ret)ret=(int)NSTEP;
done:
  /* In module, always release PA before changing synth back. */
  if(keyed){
    (void)api->tx_pa(0);
    if(snapshot){
    if(api->fd_write(FREQWORD_LO,rlo)!=0)restore_ok=0;
    if(api->fd_write(FREQWORD_HI,rhi)!=0)restore_ok=0;
    if(api->fd_write(GAIN,rg)!=0)restore_ok=0;
    if(api->fd_write(TONE,rt)!=0)restore_ok=0;
    if(api->fd_read(FREQWORD_LO)!=rlo ||
       api->fd_read(FREQWORD_HI)!=rhi)restore_ok=0;
    }
    api->tx_off();
  }
  if(!restore_ok)return -16;
  p=put(line,"tx17c n=");
  p=dec(p,ret<0?0:(uint32_t)ret);
  p=put(p," base=");p=dec(p,rlo);
  p=put(p," hi=");p=dec(p,rhi);
  p=put(p," pa=");p=dec(p,TX17_RF_ENABLE);
  *p=0;api->say(line);
  return ret;
#endif
}
