/* MIC PCM: physical GP0/PB1 high-speed DMA -> 8kHz signed PCM probe.
 * Uses RFIC mic-only service and a bounded PA-muted TX lease. Only stores
 * spectral and clipping evidence; no SPI flash writes, no RF emission.
 * SPDX-License-Identifier: MIT */
#define MIC_GP0_PCM_ONLY 1
#include "sotto_mic_gp0_dma.c"
#include "sotto_gp0_pcm.h"
#define PCM_WARM 800u
#define PCM_WINDOW 256u
#define PCM_WINDOWS 4u
#define PCM_TOTAL (PCM_WARM+PCM_WINDOW*PCM_WINDOWS)
typedef struct {
 int32_t re1,im1,re2,im2;
 uint32_t energy, clipped, count;
} pcm_metric_t;
static void pcm_metric_clear(pcm_metric_t*m){
 for(unsigned j=0;j<sizeof(*m);j++)((uint8_t*)m)[j]=0;
}
static void pcm_metric_feed(pcm_metric_t*m,int16_t sample){
 /* 1kHz = 8 samples/period; 2kHz = 4 samples/period at 8kHz PCM.
  * Sine coefficients are from the same 64-point reference table already
  * used by the native GP0 capture diagnostic. Shift 5 to keep 32bit IQ
  * sums within bound even if PCM is clipped. */
 unsigned n=m->count;
 int32_t s=(int32_t)sample>>5;
 m->re1+=s*sn[((n&7u)*8u+16u)&63u];
 m->im1+=s*sn[(n&7u)*8u];
 m->re2+=s*sn[((n&3u)*16u+16u)&63u];
 m->im2+=s*sn[(n&3u)*16u];
 m->energy+=(uint32_t)(s*s);
 if(sample==32767||sample==-32768)m->clipped++;
 m->count++;
}
static uint16_t pcm_tone(int32_t a,int32_t b,uint32_t n){
 if(!n)return 0;
 /* IQ coefficient ~127/2 and PCM shift>>5, therefore full-scale
  * sine peak approximately sqrt((I/N)^2+(Q/N)^2). */
 int32_t x=a<0?-(int32_t)divu(abs32(a),n):(int32_t)divu(abs32(a),n);
 int32_t y=b<0?-(int32_t)divu(abs32(b),n):(int32_t)divu(abs32(b),n);
 uint32_t ax=abs32(x),ay=abs32(y);
 if(ax>32767u||ay>32767u)return 65535u;
 uint32_t r=root(ax*ax+ay*ay);
 return (uint16_t)(r>65535u?65535u:r);
}
static uint8_t pcm_rms8(const pcm_metric_t*m){
 if(!m->count)return 0;
 uint32_t q=root(divu(m->energy,m->count));
 /* Q5 input shift, so RMS ~ q*32. Compress to 8bit by 64-count units. */
 return (uint8_t)((q>>1)>255u?255u:(q>>1));
}
/* Two-stage probe: first calibrate DMA sample rate using existing exact
 * timing code; then run a single uninterrupted DMA session producing 8kHz
 * PCM, discarding the first 100ms for the fixed-point DC filter to settle.
 * Never call slow firmware functions while DMA is running. */
static int capture_pcm(uint8_t*ring,uint32_t fs, uint8_t report[33],
                       uint32_t*produced_pcm){
 if(fs<35000u||fs>130000u)return -21;
 gp0_pcm_t dec;gp0_pcm_init(&dec,fs);
 pcm_metric_t stats;pcm_metric_clear(&stats);
 unsigned windows=0;
 start_dma(ring);
 uint32_t laps=0,lastw=0,consumed=0,produced=0;
 uint32_t lastclock=rd(TIM17_CNT)&0xffffu, cycles=0;
 while(dec.samples<PCM_TOTAL){
  uint32_t w=RINGSZ-(rd(DMA_CNDTR1)&0xffffu);
  if(w<lastw)laps++;
  lastw=w;produced=laps*RINGSZ+w;
  uint32_t now=rd(TIM17_CNT)&0xffffu;
  cycles+=(now-lastclock)&0xffffu;lastclock=now;
  uint32_t avail=produced-consumed;
  if(avail>RINGSZ-L){stop_dma();return -11;}
  if(avail<L)continue;
  unsigned bits=ones64(ring,consumed&(RINGSZ-1u));
  consumed+=L;
  int16_t output=0;
  int status=gp0_pcm_push(&dec,bits,&output);
  if(status<0){stop_dma();return -24;}
  if(status==1){
   unsigned index=dec.samples-1;
   if(index>=PCM_WARM){
    pcm_metric_feed(&stats,output);
    if(stats.count==PCM_WINDOW){
     uint8_t *dst=report+7u+6u*windows;
     put16(dst,pcm_tone(stats.re1,stats.im1,stats.count));
     put16(dst+2,pcm_tone(stats.re2,stats.im2,stats.count));
     dst[4]=pcm_rms8(&stats);
     dst[5]=(uint8_t)(stats.clipped>255u?255u:stats.clipped);
     windows++;
     pcm_metric_clear(&stats);
    }
   }
  }
  if(cycles>120000000u){stop_dma();return -12;}
 }
 stop_dma();
 *produced_pcm=dec.samples;
 if(windows!=PCM_WINDOWS)return -25;
 return 0;
}
int k6_main(const k6_api_t*a){
 if(!a||a->abi!=1||a->level<30)return -3;
 uint32_t space=0;uint8_t *scratch=a->scratch(&space);
 if(!scratch||space<RINGSZ+4u)return -8;
 uint8_t*ring=(uint8_t*)(((uintptr_t)scratch+3u)&~(uintptr_t)3u);
 if((rd(GPIOB)&(3u<<2))!=0)return -15;
 a->lcd_text(0,"MIC PCM / NO RF");
 a->lcd_text(1,"GP0 -> 8kHz audio");
 a->lcd_text(2,"Hold PTT ~2 seconds");
 a->lcd_flush();a->say("MAB1:CLR");
 uint32_t t=a->ms();int ptt=0;
 while(a->ms()-t<15000u){
  ptt=a->mic_stream(3,0,0);
  if(ptt<0||a->key_down())return -4;
  if(ptt==1)break;
  a->delay_us(5000);
 }
 if(ptt!=1)return -4;
 a->delay_us(30000);
 if(a->mic_stream(3,0,0)!=1)return -4;
 uint8_t report[33];for(unsigned j=0;j<33;j++)report[j]=0;report[0]=8;
 int tx=0,route=0,enabled=0,gpio=0,ret=0;
 uint32_t oldahb=0,oldapb=0, count=0;
 uint16_t old33=0,old34=0;
 if(a->tx_on(2500u)!=0){ret=-5;goto done;}
 tx=1;
 if(a->mic_stream(7,0,0)!=0){ret=-6;goto done;}
 route=1;
 old33=a->fd_read(0x33);old34=a->fd_read(0x34);
 if(a->fd_write(0x33,(uint16_t)(old33&~0x0100u))!=0){ret=-17;goto done;}
 gpio=1;
 if(a->fd_write(0x34,(uint16_t)((old34&~0xfu)|12u))!=0||
    (a->fd_read(0x34)&0xfu)!=12u){ret=-18;goto done;}
 a->delay_us(800000u);
 if(a->mic_stream(3,0,0)!=1||a->key_down()){ret=-9;goto done;}
 oldahb=rd(RCC_AHB);oldapb=rd(RCC_APB2);
 if(oldapb&TIM17EN){ret=-19;goto done;}
 wr(RCC_AHB,oldahb|1u);
 if(rd(DMA_CCR1)&1u){ret=-20;wr(RCC_AHB,oldahb);goto done;}
 wr(RCC_APB2,oldapb|TIM17EN);enabled=1;
 metrics_t m;
 ret=capture(ring,CAL_OUT,0,0,&m);
 if(ret)goto done;
 uint32_t cycles_per_1000=divu(m.cycles,1000u);
 uint32_t fs=divu(CAL_OUT*96000u,cycles_per_1000?cycles_per_1000:1u);
 if(fs<35000u||fs>130000u){ret=-21;goto done;}
 put16(report+3,divu(fs,10u));
 ret=capture_pcm(ring,fs,report,&count);
 if(ret)goto done;
 put16(report+5,count);
 put16(report+1,a->fd_read(0x64)&1023u);
done:
 if(enabled){stop_dma();wr(DMA_CNDTR1,0);wr(DMA_CPAR1,0);wr(DMA_CMAR1,0);
  wr(RCC_APB2,oldapb);wr(RCC_AHB,oldahb);}
 if(gpio){
  if(a->fd_write(0x34,old34)!=0&&!ret)ret=-22;
  if(a->fd_write(0x33,old33)!=0&&!ret)ret=-23;
 }
 if(route)a->mic_stream(1,0,0);
 if(tx)a->tx_off();
 if(ret){
  report[0]=9;report[1]=(uint8_t)(-ret);
  emit(a,report);
  a->lcd_text(0,"MIC PCM ABORT");
  a->lcd_text(1,"Read serial error");
  a->lcd_flush();return ret;
 }
 emit(a,report);
 a->lcd_text(0,"MIC PCM SAVED");
 a->lcd_text(1,"Reconnect no reboot");
 a->lcd_flush();
 uint32_t end=a->ms();
 while(a->ms()-end<6000u){if(a->key_down())break;a->delay_us(25000);}
 return 8;
}
