/* MIC TIME: no RF, time-resolved PCM sampling over the first second.
 * Compatible with installed live-serial mailbox command 0x86, without a
 * firmware reflash. No FD6818 gain or AF-route writes by this module.
 * Sampling windows are relative to first valid PCM after ISR's 3 warm ticks.
 * SPDX-License-Identifier: MIT */
#define K6_MODULE_LEVEL 30
#define K6_MODULE_OPT z
#include "../k6_mod.h"
K6_MODULE_NAME("MIC TIME");
#include "sotto_mic_cal_math.inc"

typedef struct {
  int16_t pcm[320];
  volatile uint8_t produced,consumed,offset,lost;
} pcm_ring_t;
_Static_assert(sizeof(pcm_ring_t)==644,"API30 microphone ring changed");

/* End of 12 windows in reconstructed 8-kHz samples:
 * six 20-ms windows to 120ms, then 80, 120, 160, 160, 160, 200ms.
 * 20ms bins resolve the transient, late bins verify steady-state. */
static const uint16_t bin_end[12]={160,320,480,640,800,960,1600,2560,3840,5120,6400,8000};

/* 8-bit approximately logarithmic, monotonic 16-bit PCM magnitude.
 * Codes 0..15 are exact; other codes have a 4-bit mantissa, <=6.25% step.
 * Pure integer math and no dynamic allocation. */
static uint8_t compress_pcm(uint32_t v){
  if(v<16)return (uint8_t)v;
  uint8_t exp=1;
  while(exp<15 && v>=(32u<<(exp-1u)))exp++;
  uint32_t m=v>>(exp-1u);
  if(m>31)m=31;
  return (uint8_t)((exp<<4u)|(m-16u));
}
static uint32_t expand_pcm(uint8_t code){
  if(code<16)return code;
  return (uint32_t)(16u+(code&15u))<<((code>>4u)-1u);
}
static void put16(uint8_t *p,uint32_t v){p[0]=(uint8_t)v;p[1]=(uint8_t)(v>>8);}
static uint16_t crc16_xmodem(const uint8_t *data,unsigned n){
  uint16_t crc=0;
  for(unsigned i=0;i<n;i++){
    crc^=(uint16_t)data[i]<<8;
    for(unsigned k=0;k<8;k++)crc=(uint16_t)((crc<<1)^((crc&0x8000u)?0x1021u:0u));
  }
  return crc;
}
static void emit_result(const k6_api_t*a,uint8_t raw[33]){
  static const char base64[]="ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
  put16(raw+31,crc16_xmodem(raw,31));
  char line[50];char *p=cat(line,"MAB1:");
  for(unsigned i=0;i<33;i+=3){
    uint32_t x=((uint32_t)raw[i]<<16)|((uint32_t)raw[i+1]<<8)|raw[i+2];
    *p++=base64[(x>>18)&63u];*p++=base64[(x>>12)&63u];
    *p++=base64[(x>>6)&63u];*p++=base64[x&63u];
  }
  *p=0;
  a->say(line);
}

int k6_main(const k6_api_t*a){
  if(!a||a->abi!=1||a->level<30)return -3;
  uint32_t room=0;uint8_t *scratch=a->scratch(&room);
  if(!scratch||room<sizeof(pcm_ring_t)||((uintptr_t)scratch&3u))return -8;
  pcm_ring_t*r=(pcm_ring_t*)scratch;
  a->lcd_text(0,"MIC TIME / NO RF");
  a->lcd_text(1,"1kHz at 0.1V");
  a->lcd_text(2,"Hold PTT ~1.5 sec");
  a->lcd_flush();
  a->say("MAB1:CLR"); // old completed run is now stale
  uint32_t wait=a->ms();int ptt=0;
  while(a->ms()-wait<15000u){
    ptt=a->mic_stream(3,0,0);
    if(ptt<0||a->key_down())return -4;
    if(ptt==1)break;
    a->delay_us(5000);
  }
  if(ptt!=1)return -4;
  a->delay_us(30000);
  if(a->mic_stream(3,0,0)!=1)return -4;
  for(uint32_t i=0;i<sizeof(*r);i++)scratch[i]=0;
  if(a->tx_on(2000u)!=0)return -5; // host mutes RF PA
  int started=0,ret=0;
  if(a->mic_stream(0,r,sizeof(*r))!=0){ret=-6;goto stop;}
  started=1;
  uint8_t raw[33];for(unsigned k=0;k<33;k++)raw[k]=0;raw[0]=2; // v2 time-bin report (same 44-char mailbox)
  uint32_t samples=0,clipped=0,vox=0,abs_sum=0,phase_idx=0;
  uint32_t bin_start=0;
  unsigned bin=0;
  volatile int32_t phase_sum[8];for(unsigned k=0;k<8;k++)phase_sum[k]=0;
  uint32_t start=a->ms();
  while(samples<8000u && a->ms()-start<1400u){
    if(a->mic_stream(3,0,0)!=1||a->key_down()){ret=-9;break;}
    if(r->lost){ret=-10;break;}
    uint8_t head=r->produced;
    if(head==r->consumed){a->delay_us(400);continue;}
    uint32_t off=((uint32_t)r->consumed&3u)*80u;
    for(unsigned j=0;j<80;j++){
      int32_t v=r->pcm[off+j];
      abs_sum+=absi(v);
      phase_sum[phase_idx&7u]+=v;
      phase_idx++;
      if(v==32767||v==-32768)clipped++;
      samples++;
      if(samples==bin_end[bin]){
        uint32_t n=samples-bin_start;
        raw[1u+bin*2u]=compress_pcm(__aeabi_uidiv(abs_sum,n));
        raw[2u+bin*2u]=compress_pcm(tone_1k_amp(phase_sum,n/8u));
        bin_start=samples;abs_sum=0;phase_idx=0;
        for(unsigned k=0;k<8;k++)phase_sum[k]=0;
        bin++;
      }
    }
    __asm__ volatile("" ::: "memory");
    r->consumed=(uint8_t)(r->consumed+1u);
    uint32_t v=a->fd_read(0x64)&1023u;
    if(v>vox)vox=v;
  }
  if(samples!=8000u||bin!=12u)ret=-12;
  if(r->lost)ret=-10;
  put16(raw+25,clipped);
  put16(raw+27,vox);
  put16(raw+29,samples);
stop:
  if(started)a->mic_stream(1,0,0);
  a->tx_off();
  if(ret)return ret;
  emit_result(a,raw); // only after PA muted and ISR stopped
  char line[24];char *p;
  a->lcd_text(0,"MIC TIME SAVED");
  p=cat(line,"0-20 A~");p=num(p,expand_pcm(raw[1]));
  p=cat(p," 1K~");p=num(p,expand_pcm(raw[2]));*p=0;a->lcd_text(1,line);
  p=cat(line,"20-40 A~");p=num(p,expand_pcm(raw[3]));
  p=cat(p," 1K~");p=num(p,expand_pcm(raw[4]));*p=0;a->lcd_text(2,line);
  p=cat(line,"120-200 A~");p=num(p,expand_pcm(raw[13]));
  *p=0;a->lcd_text(3,line);
  p=cat(line,"800-1000 A~");p=num(p,expand_pcm(raw[23]));
  *p=0;a->lcd_text(4,line);
  a->lcd_text(5,"Reconnect no reboot");
  a->lcd_flush();
  uint32_t end=a->ms();
  while(a->ms()-end<8000u){if(a->key_down())break;a->delay_us(30000);}
  return 12;
}
