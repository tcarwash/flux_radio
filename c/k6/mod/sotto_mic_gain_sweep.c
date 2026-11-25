/* Flux MIC GAIN SWEEP: five mic amplifier gains during one PA-muted TX lease.
 * The external generator stays at a fixed 1 kHz input. Only FD6818 REG_7D
 * low five gain bits vary; the API30 host restores the saved register at stop.
 * No FSK or RF PA enable. Requires physical PTT; not serial PTToverride.
 * SPDX-License-Identifier: MIT */
#define K6_MODULE_LEVEL 30
#define K6_MODULE_OPT z
#include "../k6_mod.h"
K6_MODULE_NAME("MIC SWEEP");

typedef struct {
  int16_t pcm[320];
  volatile uint8_t produced, consumed, offset, lost;
} pcm_ring_t;
_Static_assert(sizeof(pcm_ring_t)==644,"API30 microphone ring layout changed");

/* Reuse the exact native, phase-invariant, DC-independent 1-kHz estimator
 * qualified by MIC CAL; no independently evolved version of its math. */
#include "sotto_mic_cal_math.inc"

enum {NGAINS=5,SETTLE_MS=120,MEASURE_MS=520,TX_LEASE_MS=4500};
static const uint8_t gains[NGAINS]={31,24,16,8,0};
typedef struct {
  uint16_t tone, clipped, avg, vox;
  uint16_t samples;
  uint8_t error;
} gain_result_t;

static int drain_block(const k6_api_t *a,pcm_ring_t *ring,uint32_t *phase_index,
                       int32_t sum[8],uint32_t *absolute,uint32_t *clipped,
                       uint32_t *ns,uint32_t *vox_max,int collect){
  uint8_t head=ring->produced;
  if(head==ring->consumed)return 0;
  uint32_t offset=((uint32_t)ring->consumed&3u)*80u;
  if(collect){
    for(uint32_t i=0;i<80;i++){
      int32_t v=ring->pcm[offset+i];
      uint32_t mag=absi(v);
      *absolute+=mag;
      sum[(*phase_index)&7u]+=v;
      ++*phase_index;
      if(v==32767||v==-32768)++*clipped;
      ++*ns;
    }
  }
  __asm__ volatile("" ::: "memory");
  ring->consumed=(uint8_t)(ring->consumed+1);
  if(collect){
    uint32_t vox=a->fd_read(0x64)&1023u;
    if(vox>*vox_max)*vox_max=vox;
  }
  return 1;
}

static void show(const k6_api_t *a,const gain_result_t *results,int detail){
  char line[24];char *p;
  a->lcd_text(0,detail?"GAIN AVG VOX":"GAIN 1K  CLIP");
  for(unsigned i=0;i<NGAINS;i++){
    const gain_result_t *r=&results[i];
    p=num(line,gains[i]);
    if(r->error||r->samples<2400){p=cat(p," ERR ");p=num(p,r->samples);}
    else if(detail){
      p=cat(p," A=");p=num(p,r->avg);
      p=cat(p," V=");p=num(p,r->vox);
    }else{
      p=cat(p," 1K=");p=num(p,r->tone);
      p=cat(p," C=");p=num(p,r->clipped);
    }
    *p=0;
    a->lcd_text((int)i+1,line);
  }
  a->lcd_flush();
}

int k6_main(const k6_api_t *a){
  if(!a||a->level<30||a->abi!=1)return -3;
  uint32_t room=0;uint8_t *scratch=a->scratch(&room);
  if(!scratch||room<sizeof(pcm_ring_t)||((uintptr_t)scratch&3))return -8;
  pcm_ring_t *ring=(pcm_ring_t *)scratch;
  a->lcd_text(0,"MIC GAIN / NO RF");
  a->lcd_text(1,"1kHz tone at 0.1V");
  a->lcd_text(2,"Hold PTT ~4 sec");
  a->lcd_flush();
  int ptt=0;
  uint32_t wait=a->ms();
  while(a->ms()-wait<15000u){
    ptt=a->mic_stream(3,0,0);
    if(ptt<0||a->key_down())return -4;
    if(ptt==1)break;
    a->delay_us(5000);
  }
  if(ptt!=1)return -4;
  a->delay_us(30000);
  if(a->mic_stream(3,0,0)!=1)return -4;
  for(uint32_t i=0;i<sizeof(*ring);i++)scratch[i]=0;
  // Native digital TX lease mutes the PA before any FIFO preload.
  // No FSK writes are performed and no RF power is enabled.
  if(a->tx_on(TX_LEASE_MS)!=0)return -5;
  int started=0,ret=0;
  if(a->mic_stream(0,ring,sizeof(*ring))!=0){ret=-6;goto stop;}
  started=1;
  const uint16_t reg_base=a->fd_read(0x7d)&(uint16_t)~31u;
  gain_result_t results[NGAINS];
  for(unsigned i=0;i<NGAINS;i++){
    results[i]=(gain_result_t){0};
    uint16_t set=(uint16_t)(reg_base|gains[i]);
    if(a->fd_write(0x7d,set)<0||(a->fd_read(0x7d)&31u)!=gains[i]){
      results[i].error=1;ret=-7;break;
    }
    uint32_t start=a->ms();
    uint32_t phase=0,clipped=0,abs_total=0,ns=0,vox=0;
    volatile int32_t phase_sum[8];
    for(unsigned k=0;k<8;k++)phase_sum[k]=0;
    // The first 120 ms after each gain change are discarded, allowing
    // RFIC bias/sampler filters to settle. Keep draining its PCM ring.
    while(a->ms()-start<SETTLE_MS+MEASURE_MS){
      if(a->mic_stream(3,0,0)!=1||a->key_down()){ret=-9;break;}
      if(ring->lost){ret=-10;break;}
      int collect=(a->ms()-start>=SETTLE_MS);
      if(!drain_block(a,ring,&phase,(int32_t *)phase_sum,
                      &abs_total,&clipped,&ns,&vox,collect))a->delay_us(200);
    }
    results[i].samples=(uint16_t)ns;
    results[i].error=(uint8_t)(ret!=0||ns<2400);
    if(ns>0){
      results[i].tone=(uint16_t)tone_1k_amp(phase_sum,ns/8u);
      results[i].clipped=(uint16_t)clipped;
      results[i].avg=(uint16_t)__aeabi_uidiv(abs_total,ns);
      results[i].vox=(uint16_t)vox;
    }
    if(ret)break;
  }
stop:
  if(started)a->mic_stream(1,0,0);
  a->tx_off();
  if(ret)return ret;
  show(a,results,0);
  uint32_t end=a->ms();int detail=0;
  while(a->ms()-end<16000u){
    if(a->key_down())break;
    if(!detail&&a->ms()-end>=8000u){show(a,results,1);detail=1;}
    a->delay_us(30000);
  }
  return 5;
}
