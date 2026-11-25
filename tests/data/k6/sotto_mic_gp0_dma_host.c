/* Executes the production MIC GP0 module with fake register/DMA source.
 * Provides deterministic 0/1kHz/2kHz sigma-delta GP0 streams. MIT. */
#include <stdint.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include <math.h>
#define MIC_GP0_HOST_TEST 1
#include "../../../c/k6/mod/sotto_mic_gp0_dma.c"
static uint8_t scratch_buf[4096];
static uint16_t regs[128];
static uint32_t ahb,apb2,dma_ccr1,dma_cndtr,dma_pos,clk,clock_ms;
static uint32_t rawseq,tx_count,tx_off_count,mic_on,mic_off,result_count,clear_count;
static int signal_hz=1000,uart_present=0,old_firmware=0,dma_busy=0,route=0,tx_active=0;
static char mailbox[52];
uint32_t mic_gp0_host_rd(uint32_t a){
 if(a==GPIOB)return 0;
 if(a==RCC_AHB)return ahb;
 if(a==RCC_APB2)return apb2;
 if(a==DMA_CCR1)return dma_ccr1;
 if(a==TIM17_CNT)return clk&0xffffu;
 if(a==DMA_CNDTR1){
  if(!(dma_ccr1&1u))return dma_cndtr;
  /* 64 GP0 raw bits written by DMA on each polling iteration. One
   * 64-bit slice is an 85.94kHz density sample at 5.5MS/s raw rate. */
  double phase=6.283185307179586*signal_hz*rawseq/85937.5;
  int ones=32+(signal_hz?(int)lrint(18*sin(phase)):0);
  for(unsigned j=0;j<64;j++){
   uint8_t high=(uint8_t)((((j*ones)&63u)<(unsigned)ones)?2:0);
   scratch_buf[dma_pos]=high;
   dma_pos=(dma_pos+1u)&(RINGSZ-1u);
  }
  rawseq++;
  clk+=1117u;
  dma_cndtr=RINGSZ-dma_pos;
  return dma_cndtr;
 }
 return 0;
}
void mic_gp0_host_wr(uint32_t a,uint32_t v){
 if(a==RCC_AHB)ahb=v;
 if(a==RCC_APB2)apb2=v;
 if(a==DMA_CCR1)dma_ccr1=v;
 if(a==DMA_CNDTR1){dma_cndtr=v;dma_pos=0;}
 if(a==TIM17+0x14u)clk=0;
}
static uint32_t ms(void){return ++clock_ms;}
static void delay_us(uint32_t us){clock_ms+=(us+999u)/1000u;}
static int key_down(void){return 0;}
static void*scratch(uint32_t*n){*n=sizeof(scratch_buf);return scratch_buf;}
static int tx_on(uint32_t ms){
 if(ms!=2500||tx_active)return -1;
 tx_count++;tx_active=1;
 regs[0x30]=0xc1fa;regs[0x33]=0xd320;return 0;
}
static void tx_off(void){
 tx_active=0;tx_off_count++;regs[0x30]=0xc1fa;regs[0x33]=0xd300;
}
static uint16_t fd_read(uint8_t reg){
 if(reg==0x64)return signal_hz?650:180;
 return regs[reg];
}
static int fd_write(uint8_t reg,uint16_t val){
 if(reg==0x33&&((regs[reg]^val)&~0x0101u))return -1;
 if(reg==0x30||reg==0x36||reg==0x37)return -1;
 regs[reg]=val;return 0;
}
static int mic_stream(uint32_t op,void*p,uint32_t n){
 (void)p;(void)n;
 if(op==3)return uart_present?-1:1;
 if(op==7){
  if(old_firmware||!tx_active||route)return -1;
  route=1;mic_on++;regs[0x30]|=0x0204;return 0;
 }
 if(op==1){
  if(route){route=0;mic_off++;regs[0x30]=0xc1fa;}
  return 0;
 }
 return -1;
}
static void lcd_text(int line,const char*s){(void)line;(void)s;}
static int lcd_flush(void){return 0;}
static void say(const char*s){
 if(strcmp(s,"MAB1:CLR")==0){clear_count++;mailbox[0]=0;return;}
 if(strncmp(s,"MAB1:",5)==0){if(strlen(s)!=49)abort();strcpy(mailbox,s);result_count++;}
}
int main(int argc,char**argv){
 if(argc<2)return 2;
 if(strcmp(argv[1],"silent")==0)signal_hz=0;
 else if(strcmp(argv[1],"2k")==0)signal_hz=2000;
 else if(strcmp(argv[1],"uart")==0)uart_present=1;
 else if(strcmp(argv[1],"oldfirmware")==0)old_firmware=1;
 else if(strcmp(argv[1],"busy")==0)dma_busy=1;
 else if(strcmp(argv[1],"1k")!=0)return 2;
 regs[0x33]=0xd300;regs[0x34]=0;regs[0x30]=0xc1fa;
 dma_ccr1=dma_busy?1u:0u;
 k6_api_t api={0};api.level=31;api.abi=1;
 api.scratch=scratch;api.ms=ms;api.delay_us=delay_us;
 api.key_down=key_down;api.tx_on=tx_on;api.tx_off=tx_off;
 api.fd_read=fd_read;api.fd_write=fd_write;api.mic_stream=mic_stream;
 api.lcd_text=lcd_text;api.lcd_flush=lcd_flush;api.say=say;
 int ret=k6_main(&api);
 if(uart_present){if(ret!=-4||tx_count||result_count||clear_count!=1)return 11;
  puts("UART EXCLUSION PASS");return 0;}
 if(old_firmware){
  if(ret!=-6||!tx_off_count||route||mic_on||result_count!=1)return 12;
  printf("encoded=%s\n",mailbox);puts("OLD FIRMWARE REJECT PASS");return 0;
 }
 if(dma_busy){
  if(ret!=-20||!tx_off_count||!mic_off||route||result_count!=1)return 13;
  printf("encoded=%s\n",mailbox);puts("DMA BUSY RESTORE PASS");return 0;
 }
 if(ret!=4||!tx_count||!tx_off_count||!mic_on||!mic_off||route||
    regs[0x33]!=0xd300||regs[0x34]!=0||regs[0x30]!=0xc1fa||
    ahb||apb2||dma_ccr1||result_count!=1||clear_count!=1)return 14;
 printf("encoded=%s\n",mailbox);
 printf("GP0 DMA AND TX MIC RESTORE PASS hz=%d rawseq=%u\n",signal_hz,rawseq);
 return 0;
}
