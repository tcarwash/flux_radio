/* Runtime tests of the exact PIC DMA1 CH1 IRQ producer against a fake NVIC,
 * DMA ring, physical GPIO 1-bit density and 8kHz PCM queue. SPDX MIT. */
#include <stdint.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include <math.h>
#define GPIRQ_HOST_TEST 1
#include "../../../c/k6/mod/sotto_gp0_irq_capture.h"
static uint32_t isr,ccr1,nvic,priority=0x20u,primask,vector=0x08002001u;
static uint32_t cndtr, dma_ifcr_calls, timer_ticks, tx_was_muted=1;
static uint8_t raw[GPIRQ_RING_SIZE] __attribute__((aligned(4)));
static int signal_hz=1000;
static uint32_t counter;
uint32_t gpirq_host_rd(uint32_t a){
 switch(a){
  case GPIRQ_CFGR1:return 3u;
  case GPIRQ_VECTOR:case 0x00000064u:return vector;
  case GPIRQ_DMA_CCR1:return ccr1;
  case GPIRQ_DMA_ISR:return isr;
  case GPIRQ_NVIC_ISER:return nvic;
  case GPIRQ_NVIC_IPR9:return priority;
  case GPIRQ_TIM17_CNT:timer_ticks+=2250;return timer_ticks;
  case GPIRQ_DMA_CNDTR1:return cndtr;
 }
 return 0;
}
void gpirq_host_wr(uint32_t a,uint32_t v){
 switch(a){
  case GPIRQ_VECTOR:vector=v;break;
  case GPIRQ_DMA_CCR1:ccr1=v;break;
  case GPIRQ_DMA_CNDTR1:cndtr=v;break;
  case GPIRQ_DMA_IFCR:isr&=~(v&15u);dma_ifcr_calls++;break;
  case GPIRQ_NVIC_ISER:nvic|=v;break;
  case GPIRQ_NVIC_ICER:nvic&=~v;break;
  case GPIRQ_NVIC_ICPR:break;
  case GPIRQ_NVIC_IPR9:priority=v;break;
 }
}
static void fill(unsigned half,unsigned fs){
 uint8_t*dst=raw+GPIRQ_HALF*half;
 unsigned samples=GPIRQ_HALF/64;
 for(unsigned i=0;i<samples;i++){
  double ph=6.283185307179586*signal_hz*counter/fs;
  int ones=32+(signal_hz?(int)lround(5.0*sin(ph)):0);
  for(unsigned j=0;j<64;j++){
   dst[i*64+j]=(uint8_t)((((j*ones)&63u)<(unsigned)ones)?2u:0u);
  }
  counter++;
 }
}
int main(int argc,char**argv){
 if(argc!=2)return 2;
 int mode=0;
 if(!strcmp(argv[1],"one"))signal_hz=1000;
 else if(!strcmp(argv[1],"two"))signal_hz=2000;
 else if(!strcmp(argv[1],"silent"))signal_hz=0;
 else if(!strcmp(argv[1],"busy"))mode=1;
 else if(!strcmp(argv[1],"late"))mode=2;
 else if(!strcmp(argv[1],"stall"))mode=3;
 else return 2;
 sl_pcm_t pcm={0};gp0_irq_audio_t ctx;
 int rc=gp0_irq_arm(&ctx,&pcm,raw,87770u);
 if(rc)return 10;
 if(!ctx.active||!(ccr1&GPIRQ_CHEN)||!(nvic&GPIRQ_NVIC_MASK)||vector!=0x20002001u)return 11;
 if(mode==1){
  /* Unexpected TC before first HT; fail closed and restore IRQ vector */
  isr=GPIRQ_FLAG_TC;gp0_irq_body(&ctx);
  if(ctx.error!=2||ctx.active||ccr1)return 12;
  gp0_irq_disarm(&ctx);
  if(nvic||priority!=0x20u||vector!=0x08002001u||gp0_irq_context_word)return 13;
  printf("FAIL-CLOSED NONSEQUENTIAL IRQ PASS\n");return 0;
 }
 if(mode==2){
  isr=GPIRQ_FLAG_HT|GPIRQ_FLAG_TC;gp0_irq_body(&ctx);
  if(ctx.error!=2||ctx.missed_halves!=1)return 14;
  gp0_irq_disarm(&ctx);
  printf("FAIL-CLOSED MISSED HALF PASS\n");return 0;
 }
 uint32_t blocks=0,unconsumed_max=0,interrupts_target=0;
 /* ~one second of half IRQs (512/5.615MS/s ~=91 us). */
 for(unsigned n=0;n<11000;n++){
  unsigned half=n&1u;
  fill(half,87770u);
  isr=half?GPIRQ_FLAG_TC:GPIRQ_FLAG_HT;
  gp0_irq_body(&ctx);
  if(ctx.error){
   if(mode==3&&ctx.error==3)break;
   fprintf(stderr,"irq failed at %u err=%u peak=%u cycles\n",n,ctx.error,ctx.peak_cycles);return 15;
  }
  if((uint8_t)(pcm.produced-pcm.consumed)>unconsumed_max)
   unconsumed_max=(uint8_t)(pcm.produced-pcm.consumed);
  /* Foreground may spend long consecutive intervals on codec/SPI; at
   * every 80 samples, consume one block and pretend to encode. */
  if(mode!=3||n%1100u<275u){
   if(pcm.produced!=pcm.consumed){pcm.consumed++;blocks++;}
  }
 }
 gp0_irq_disarm(&ctx);
 if(vector!=0x08002001u||priority!=0x20u||nvic||
    gp0_irq_context_word||ccr1||ctx.active)return 16;
 if(mode==3){
  /* A 75ms foreground stall exhausts the 40ms PCM ring: detect fail. */
  if(!ctx.error||!pcm.lost)return 17;
  printf("FOREGRD 75MS STALL CORRECTLY FAILED\n");return 0;
 }
 if(ctx.error||ctx.actual_halves!=11000||ctx.interrupts!=11000||
    ctx.dec.samples<7800||ctx.dec.samples>8300||blocks<95||
    unconsumed_max>4||ctx.peak_cycles>=9000)return 18;
 printf("CONTINUOUS ISR PASS samples=%u blocks=%u max_backlog=%u peak_mock_cycles=%u dma_irqs=%u hz=%d\n",
       ctx.dec.samples,blocks,unconsumed_max,ctx.peak_cycles,ctx.interrupts,signal_hz);
 return 0;
}
