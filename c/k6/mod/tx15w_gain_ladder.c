/* TX-15W: amplitude-of-FM-deviation control via REG_70 gain ladder.
 * Thirteen 85ms plateaus: seven gain24 reference segments interleaved
 * with 8,16,32,48,64,80. REG_71 is held at a 1091Hz word throughout.
 * PA stays keyed; never writes APC, PLL, mic, I2S or user flash.
 * Explicit ACK gates; inert default build. Bounded dummy-load only.
 * SPDX-License-Identifier: MIT.
 */
#define K6_MODULE_LEVEL 3
#include "../k6_mod.h"
#ifndef TX15W_BENCH_ENABLE
#define TX15W_BENCH_ENABLE 0
#endif
#ifndef TX15W_RF_ENABLE
#define TX15W_RF_ENABLE 0
#endif
#ifndef TX15W_FIXTURE_ACK
#define TX15W_FIXTURE_ACK 0
#endif
#ifndef TX15W_WATCHDOG_ACK
#define TX15W_WATCHDOG_ACK 0
#endif
#ifndef TX15W_HZ
#define TX15W_HZ 433500000u
#endif
#if TX15W_RF_ENABLE && (!TX15W_BENCH_ENABLE || !TX15W_FIXTURE_ACK || !TX15W_WATCHDOG_ACK)
#error "TX15W requires explicit bench/fixture/watchdog RF ACKs"
#endif
#if TX15W_HZ != 433500000u
#error "TX15W fixed 433500000Hz conducted bench only"
#endif
#define W_LEASE 1800u
#define W_AUDIO_WORD 11264u
#define W_N 13u
#define W_SLOT_US 85000u
#define W_LEAD_US 80000u
#define W_TAIL_US 100000u
#if TX15W_BENCH_ENABLE
static const uint8_t gains[W_N]={24,8,24,16,24,32,24,48,24,64,24,80,24};
static char *put(char *p,const char *s){while(*s)*p++=*s++;return p;}
static char *dec(char *p,uint32_t v){
  static const uint32_t places[]={
    1000000000u,100000000u,10000000u,1000000u,
    100000u,10000u,1000u,100u,10u,1u};
  int i,started=0;
  for(i=0;i<10;i++){
    uint32_t d=0;while(v>=places[i]){v-=places[i];d++;}
    if(d||started||i==9){*p++=(char)('0'+d);started=1;}
  }
  return p;
}
#endif
int k6_main(const k6_api_t *api){
  if(api->abi!=1||api->level<3||api->fd_read(0)!=0x4829u)return -3;
#if !TX15W_BENCH_ENABLE
  api->say("tx15w disabled; explicit bench wrapper required");
  return -9;
#else
  uint16_t rg=0,rt=0;
  int ret=0,keyed=0,snapshot=0,restored=1;
  uint32_t i;
  char line[60],*p;
  if(api->rx_hz()!=TX15W_HZ||api->key_down())return -2;
  if(KM_RSSI_DBM(api->fd_read(0x67))>-90)return -7;
  api->delay_us(50000u);
  if(KM_RSSI_DBM(api->fd_read(0x67))>-90)return -7;
  if(api->tx_on(W_LEASE)!=0)return -5;
  keyed=1;
  if(api->tx_pa(0)!=0){ret=-12;goto done;}
  rg=api->fd_read(0x70);
  rt=api->fd_read(0x71);
  snapshot=1;
  if(api->fd_write(0x70,0)!=0){ret=-12;goto done;}
  if(api->fd_write(0x71,W_AUDIO_WORD)!=0){ret=-12;goto done;}
  api->delay_us(W_LEAD_US);
  if(api->fd_write(0x70,(uint16_t)(0x8000u|((uint16_t)gains[0]<<8)))!=0){
    ret=-12;goto done;
  }
#if TX15W_RF_ENABLE
  if(api->tx_pa(1)!=0){ret=-12;goto done;}
#endif
  for(i=0;i<W_N;i++){
    uint16_t target=(uint16_t)(0x8000u|((uint16_t)gains[i]<<8));
    if(api->key_down()){ret=-4;break;}
    if(i!=0 && api->fd_write(0x70,target)!=0){ret=-12;break;}
    if(api->fd_read(0x70)!=target){ret=-14;break;}
    api->delay_us(W_SLOT_US);
  }
  if(!ret){
    if(api->fd_write(0x70,0)!=0){ret=-12;goto done;}
    if(api->fd_read(0x70)!=0){ret=-14;goto done;}
    api->delay_us(W_TAIL_US);
    ret=(int)W_N;
  }
done:
  if(keyed){
    (void)api->tx_pa(0);
    if(snapshot){
      if(api->fd_write(0x70,rg)!=0)restored=0;
      if(api->fd_write(0x71,rt)!=0)restored=0;
      if(api->fd_read(0x70)!=rg || api->fd_read(0x71)!=rt)restored=0;
    }
    api->tx_off();
  }
  if(!restored)return -16;
  p=put(line,"tx15w n=");p=dec(p,ret<0?0:(uint32_t)ret);
  p=put(p," tone=");p=dec(p,W_AUDIO_WORD);
  p=put(p," pa=");p=dec(p,TX15W_RF_ENABLE);
  *p=0;api->say(line);
  return ret;
#endif
}
