/* MIC IRQ: prove live, interrupt-preempted GP0 DMA -> 8kHz PCM producer.
 * PA-muted TX lease, RFIC microphone route only, no RF power, no codec.
 * The PCM ring is consumed in foreground with simulated 2ms/10ms work.
 * Records ISR cycle peaks, queue/IRQ errors and an actual PCM signal level.
 * API30, uses the SRAM-mapped IRQ9 vector only while TXdog is armed.
 * SPDX: MIT */
#define MIC_GP0_PCM_ONLY 1
#define MIC_GP0_IRQ_ONLY 1
#include "sotto_mic_gp0_dma.c"
#include "sotto_gp0_irq_capture.h"
static uint32_t min32(uint32_t a,uint32_t b){return a<b?a:b;}
static uint16_t quant16(uint32_t x){return (uint16_t)min32(x,65535u);}
int k6_main(const k6_api_t*a){
 if(!a||a->level<30||a->abi!=1)return -3;
 uint32_t room=0;uint8_t *scratch=a->scratch(&room);
 if(!scratch||room<GPIRQ_RING_SIZE+sizeof(sl_pcm_t)+sizeof(gp0_irq_audio_t)+16u)return -8;
 uint8_t*raw=(uint8_t*)(((uintptr_t)scratch+3u)&~(uintptr_t)3u);
 sl_pcm_t*pcm=(sl_pcm_t*)(raw+GPIRQ_RING_SIZE);
 gp0_irq_audio_t*irq=(gp0_irq_audio_t*)(raw+GPIRQ_RING_SIZE+sizeof(*pcm));
 for(unsigned i=0;i<sizeof(*pcm);i++)((uint8_t*)pcm)[i]=0;
 if((rd(GPIOB)&(3u<<2))!=0)return -15;
 a->lcd_text(0,"MIC IRQ / NO RF");
 a->lcd_text(1,"DMA continuous PCM");
 a->lcd_text(2,"Hold PTT ~2 seconds");
 a->lcd_flush();a->say("MAB1:CLR");
 uint32_t t=a->ms();int ptt=0;
 while(a->ms()-t<15000u){
  ptt=a->mic_stream(3,0,0);
  if(ptt<0||a->key_down())return -4;
  if(ptt==1)break;
  a->delay_us(5000u);
 }
 if(ptt!=1)return -4;
 a->delay_us(30000u);
 if(a->mic_stream(3,0,0)!=1)return -4;
 uint8_t report[33];for(unsigned j=0;j<33;j++)report[j]=0;report[0]=10;
 int tx=0,route=0,enabled=0,gpio=0,armed=0,ret=0;
 uint32_t oldahb=0,oldapb=0,blocks=0,samples=0,peak=0,rms_acc=0;
 uint16_t old33=0,old34=0;
 uint32_t maxback=0,maxvox=0;
 if(a->tx_on(2500u)){ret=-5;goto done;}
 tx=1;
 if(a->mic_stream(7,0,0)){ret=-6;goto done;}
 route=1;
 old33=a->fd_read(0x33);old34=a->fd_read(0x34);
 if(a->fd_write(0x33,(uint16_t)(old33&~0x0100u))){ret=-17;goto done;}
 gpio=1;
 if(a->fd_write(0x34,(uint16_t)((old34&~0xfu)|12u))||
    (a->fd_read(0x34)&0xfu)!=12u){ret=-18;goto done;}
 a->delay_us(800000u);
 if(a->mic_stream(3,0,0)!=1||a->key_down()){ret=-9;goto done;}
 oldahb=rd(RCC_AHB);oldapb=rd(RCC_APB2);
 if(oldapb&TIM17EN){ret=-19;goto done;}
 wr(RCC_AHB,oldahb|1u);
 if(rd(DMA_CCR1)&1u){ret=-20;wr(RCC_AHB,oldahb);goto done;}
 wr(RCC_APB2,oldapb|TIM17EN);enabled=1;
 metrics_t calibration;
 ret=capture(raw,CAL_OUT,0,0,&calibration);
 if(ret)goto done;
 uint32_t cyc=divu(calibration.cycles,1000u);
 uint32_t fs=divu(CAL_OUT*96000u,cyc?cyc:1u);
 if(fs<35000u||fs>130000u){ret=-21;goto done;}
 put16(report+3,divu(fs,10u));
 /* The calibrated legacy DMA capture always stops TIM17. Restart the
  * counter before IRQ mode, else the 96MHz ISR cycle guard reads zero and
  * falsely claims timing success. UDE does not enable TIM17 interrupts. */
 wr(TIM17+0x28u,0);
 wr(TIM17+0x2cu,0xffffu);
 wr(TIM17+0x14u,1u);
 wr(TIM17+0x10u,0u);
 wr(TIM17+0x0cu,1u<<8);
 wr(TIM17,1u);
 /* Capture 600ms while intentionally inserting ~2ms/10ms of foreground
  * inactivity (as if codec were busy). DMA HT/TC interrupts must continue
  * preemptively, or the PCM FIFO will overrun. */
 ret=gp0_irq_arm(irq,pcm,raw,fs);
 if(ret){ret=-26;goto done;}
 armed=1;
 uint32_t began=a->ms();
 while(a->ms()-began<600u){
  if(irq->error||pcm->lost){ret=-27;break;}
  unsigned depth=(uint8_t)(pcm->produced-pcm->consumed);
  if(depth>maxback)maxback=depth;
  if(depth){
   unsigned slot=pcm->consumed&(SL_PCM_BLOCKS-1u);
   for(unsigned j=0;j<SL_PCM_SAMPLES;j++){
    int32_t s=(int32_t)pcm->pcm[slot][j];
    rms_acc+=(uint32_t)((s>>7)*(s>>7));
    uint32_t aa=(uint32_t)(s<0?-s:s);
    if(aa>peak)peak=aa;
   }
   pcm->consumed++;samples+=SL_PCM_SAMPLES;blocks++;
   /* Allow IRQs during a mocked 2ms codec step. */
   a->delay_us(2000u);
  }else a->delay_us(200u);
 }
 if(armed){gp0_irq_disarm(irq);armed=0;}
 if(!ret&&irq->error)ret=-27;
 if(!ret&&pcm->lost)ret=-28;
 if(!ret&&(blocks<35u||irq->actual_halves<3500u))ret=-29;
 if(!ret){
  maxvox=a->fd_read(0x64)&1023u;
  put16(report+1,maxvox);
  put16(report+5,quant16(irq->dec.samples));
  put16(report+7,quant16(irq->interrupts));
  put16(report+9,quant16(blocks));
  put16(report+11,quant16(irq->peak_cycles));
  put16(report+13,quant16(divu(irq->work_cycles,irq->interrupts?irq->interrupts:1u)));
  put16(report+15,quant16(irq->dec.clipped));
  put16(report+17,quant16(irq->missed_halves));
  report[19]=(uint8_t)min32(maxback,255u);
  report[20]=(uint8_t)irq->error;
  put16(report+21,quant16(peak));
  put16(report+23,quant16(samples));
  put16(report+25,quant16(root(divu(rms_acc,samples?samples:1u))*128u));
 }
done:
 if(armed)gp0_irq_disarm(irq);
 if(enabled){stop_dma();wr(DMA_CNDTR1,0);wr(DMA_CPAR1,0);wr(DMA_CMAR1,0);
  wr(RCC_APB2,oldapb);wr(RCC_AHB,oldahb);}
 if(gpio){
  if(a->fd_write(0x34,old34)&&!ret)ret=-22;
  if(a->fd_write(0x33,old33)&&!ret)ret=-23;
 }
 if(route)a->mic_stream(1,0,0);
 if(tx)a->tx_off();
 if(ret){
  report[0]=11;report[1]=(uint8_t)(-ret);
  if(enabled){
   report[2]=irq->error;
   put16(report+3,quant16(irq->interrupts));
   put16(report+5,quant16(irq->peak_cycles));
  }
  emit(a,report);
  a->lcd_text(0,"MIC IRQ ABORT");a->lcd_text(1,"Error saved to serial");
  a->lcd_flush();return ret;
 }
 emit(a,report);
 a->lcd_text(0,"MIC IRQ SAVED");a->lcd_text(1,"Reconnect no reboot");
 a->lcd_flush();
 uint32_t end=a->ms();
 while(a->ms()-end<6000u){if(a->key_down())break;a->delay_us(25000u);}
 return (int)blocks;
}
