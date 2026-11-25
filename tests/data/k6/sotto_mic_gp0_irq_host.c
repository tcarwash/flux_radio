/* Native whole-module fake RFIC + DMA1/IRQ9 + GPIO1 PDM on the K6.
 * Actual production 8kHz DMA IRQ handler executes in each synthetic half.
 * Does not pretend to model Cortex-M0 interrupt/codec cycles. MIT. */
#include <stdint.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include <math.h>
#define MIC_GP0_HOST_TEST 1
#define GPIRQ_HOST_TEST 1
#include "../../../c/k6/mod/sotto_mic_gp0_irq.c"
static uint8_t scratch_buf[8192] __attribute__((aligned(4)));
static uint16_t regs[128];
static uint32_t ahb,apb2,ccr1,cndtr,dmapos,clock_us,clock_ms,clk,rawseq,timcr1;
static uint32_t nvic,vector=0x20004001u,priority=0x50u,dma_flags;
static uint32_t tx_count,tx_off_count,route_count,route_off_count,results,clears;
static int signal_hz=1000,mode=0,route=0,tx=0,irq_simulations=0;
static char mailbox[52];
static void fill_64(uint8_t *dest,uint32_t idx){
 double ph=6.283185307179586*signal_hz*idx/86000.0;
 int bits=32+(signal_hz?(int)lround(4.0*sin(ph)):0);
 for(unsigned j=0;j<64;j++)dest[j]=(uint8_t)((((j*bits)&63u)<(unsigned)bits)?2:0);
}
static void pump(unsigned ms){
 /* Real DMA has ~11k half-buffer IRQs per second and the Cortex-M0
  * foreground is preemptible. Model IRQs while a->delay_us() blocks. */
 gp0_irq_audio_t *s=(gp0_irq_audio_t*)(scratch_buf+GPIRQ_RING_SIZE+sizeof(sl_pcm_t));
 if(!(nvic&GPIRQ_NVIC_MASK)||!(ccr1&GPIRQ_CHEN)||!(ccr1&GPIRQ_IRQ_ENABLE))return;
 for(unsigned n=0;n<11u*ms;n++){
  unsigned half=irq_simulations++&1u;
  uint8_t *dst=scratch_buf+half*GPIRQ_HALF;
  for(unsigned i=0;i<GPIRQ_HALF;i+=64){fill_64(dst+i,rawseq++);}
  dma_flags|=half?GPIRQ_FLAG_TC:GPIRQ_FLAG_HT;
  clk+=8800u;
  gp0_irq_body(s);
  if(s->error)break;
 }
}
uint32_t mic_gp0_host_rd(uint32_t a){
 if(a==GPIOB)return 0;
 if(a==RCC_AHB)return ahb;
 if(a==RCC_APB2)return apb2;
 if(a==DMA_CCR1)return ccr1;
 if(a==TIM17_CNT)return clk&0xffffu;
 if(a==DMA_CNDTR1){
  if(!(ccr1&1u))return cndtr;
  if(ccr1&GPIRQ_IRQ_ENABLE)return cndtr;
  /* The calibration uses the original polling DMA path (64 bytes/turn). */
  fill_64(scratch_buf+dmapos,rawseq++);
  dmapos=(dmapos+64u)&(RINGSZ-1u);
  clk+=1117u;
  return cndtr=RINGSZ-dmapos;
 }
 return 0;
}
void mic_gp0_host_wr(uint32_t a,uint32_t v){
 if(a==RCC_AHB)ahb=v;
 if(a==RCC_APB2)apb2=v;
 if(a==DMA_CCR1)ccr1=v;
 if(a==TIM17)timcr1=v;
 if(a==DMA_CNDTR1){cndtr=v;dmapos=0;}
 if(a==TIM17+0x14u)clk=0;
}
uint32_t gpirq_host_rd(uint32_t a){
 if(a==GPIRQ_CFGR1)return mode==3?2u:3u;
 if(a==GPIRQ_VECTOR||a==0x00000064u)return vector;
 if(a==GPIRQ_DMA_CCR1)return ccr1;
 if(a==GPIRQ_DMA_CNDTR1)return cndtr;
 if(a==GPIRQ_DMA_ISR)return dma_flags;
 if(a==GPIRQ_NVIC_ISER)return nvic;
 if(a==GPIRQ_NVIC_IPR9)return priority;
 if(a==GPIRQ_TIM17_CNT){if(timcr1&1u)clk+=700u;return clk;}
 return 0;
}
void gpirq_host_wr(uint32_t a,uint32_t v){
 if(a==GPIRQ_VECTOR)vector=v;
 if(a==GPIRQ_DMA_CCR1)ccr1=v;
 if(a==GPIRQ_DMA_CNDTR1)cndtr=v;
 if(a==GPIRQ_NVIC_ISER)nvic|=v;
 if(a==GPIRQ_NVIC_ICER)nvic&=~v;
 if(a==GPIRQ_NVIC_IPR9)priority=v;
 if(a==GPIRQ_DMA_IFCR)dma_flags&=~(v&15u);
}
static uint32_t ms(void){clock_ms++;pump(1);return clock_ms;}
static void delay_us(uint32_t us){unsigned adv=(us+999u)/1000u;clock_ms+=adv;
 if(mode==4&&irq_simulations>1000u)adv+=70u;
 pump(adv);
}
static int key_down(void){return 0;}
static void*scratch(uint32_t*n){*n=sizeof(scratch_buf);return scratch_buf;}
static int tx_on(uint32_t limit){if(limit!=2500||tx)return -1;tx=1;tx_count++;regs[0x33]=0xd320;return 0;}
static void tx_off(void){tx=0;tx_off_count++;regs[0x33]=0xd300;}
static uint16_t fd_read(uint8_t r){return r==0x64?(signal_hz?600u:170u):regs[r];}
static int fd_write(uint8_t r,uint16_t v){
 if(r==0x33&&((regs[r]^v)&~0x0101u))return -1;
 regs[r]=v;return 0;
}
static int mic_stream(uint32_t op,void*p,uint32_t n){(void)p;(void)n;
 if(op==3)return mode==1?-1:1;
 if(op==7){if(mode==2||!tx||route)return -1;route=1;route_count++;return 0;}
 if(op==1){if(route){route=0;route_off_count++;}return 0;}
 return -1;
}
static void lcd_text(int i,const char*s){(void)i;(void)s;}
static int lcd_flush(void){return 0;}
static void say(const char*s){
 if(!strcmp(s,"MAB1:CLR")){clears++;mailbox[0]=0;return;}
 if(!strncmp(s,"MAB1:",5)){if(strlen(s)!=49)abort();strcpy(mailbox,s);results++;}
}
int main(int argc,char**argv){
 if(argc!=2)return 2;
 if(!strcmp(argv[1],"silent"))signal_hz=0;
 else if(!strcmp(argv[1],"2k"))signal_hz=2000;
 else if(!strcmp(argv[1],"uart"))mode=1;
 else if(!strcmp(argv[1],"oldfirmware"))mode=2;
 else if(!strcmp(argv[1],"novector"))mode=3;
 else if(!strcmp(argv[1],"stall"))mode=4;
 else if(strcmp(argv[1],"1k"))return 2;
 regs[0x33]=0xd300;
 k6_api_t api={0};api.abi=1;api.level=31;
 api.scratch=scratch;api.ms=ms;api.delay_us=delay_us;
 api.key_down=key_down;api.tx_on=tx_on;api.tx_off=tx_off;
 api.fd_read=fd_read;api.fd_write=fd_write;api.mic_stream=mic_stream;
 api.lcd_text=lcd_text;api.lcd_flush=lcd_flush;api.say=say;
 int ret=k6_main(&api);
 if(mode==1){if(ret!=-4||tx||tx_count||results||clears!=1)return 11;
  puts("UART EXCLUSION PASS");return 0;}
 if(mode==2){if(ret!=-6||tx||!tx_off_count||route||results!=1)return 12;}
 else if(mode==3){if(ret!=-26||tx||!tx_off_count||route||results!=1)return 13;}
 else if(mode==4){if(ret!=-27||tx||!tx_off_count||route||results!=1)return 14;}
 else if(ret<35||ret>110||!tx_count||!tx_off_count||!route_count||!route_off_count||
         results!=1||clears!=1)return 15;
 if(ccr1||nvic||vector!=0x20004001u||priority!=0x50u||
    regs[0x33]!=0xd300||regs[0x34]!=0||route||tx)return 16;
 printf("encoded=%s\n",mailbox);
 printf("CONTINUOUS GP0 IRQ MODULE PASS hz=%d blocks=%d interrupts=%u\n",
        signal_hz,ret,irq_simulations);
 return 0;
}
